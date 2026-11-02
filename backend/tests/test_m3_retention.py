"""M3 garbage-collection roots never imply a live sharing permission."""
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.modules.community.models import CommunityAsset, CommunityPost, CommunitySnapshot
from app.modules.exports.social_models import SocialArchiveAsset
from app.modules.files.retention import RetentionError, collect_references
from app.modules.notes.models import Note
from app.modules.social.chat_models import ChatAttachment, ChatMessage
from app.modules.social.models import Friendship, NoteGrant
from app.shared.db import SessionLocal, engine, utcnow
from app.shared.models import Job
from test_m3_site_backup import m3_backup_account


def collect():
    with engine.connect() as connection:
        return collect_references(connection)


def expected_roots(a):
    keys = set(a.contents)
    with SessionLocal() as db:
        for asset in db.scalars(select(CommunityAsset)):
            keys.update((asset.object_key, asset.original_object_key))
            if asset.source_object_key:
                keys.add(asset.source_object_key)
            if asset.source_original_object_key:
                keys.add(asset.source_original_object_key)
    return keys


def test_chat_community_and_offline_assets_are_deterministic_retention_roots(m3_backup_account):
    a = m3_backup_account
    result = collect()
    assert result["keys"] == expected_roots(a)
    assert result["active_writers"] == 1
    assert result["counts"]["chat_attachments"] == 1
    assert result["counts"]["community_assets"] == 5
    assert result["counts"]["social_archive_assets"] == 1
    assert collect() == result


def test_source_removal_withdraw_unfriend_and_revocation_never_drop_frozen_roots(m3_backup_account):
    a = m3_backup_account
    before = collect()
    with SessionLocal.begin() as db:
        # Community FK SET NULL does not delete snapshots or their copied files.
        db.delete(db.get(Note, a.source_note))
        db.get(CommunityPost, a.posts[0]).status = "withdrawn"
        db.scalar(select(Friendship)).status = "removed"
        grant = db.scalar(select(NoteGrant))
        grant.revoked_at = utcnow()
        grant.revision += 1
    after = collect()
    assert after["keys"] == before["keys"] == expected_roots(a)
    assert after["fingerprint"] != before["fingerprint"]


def test_collector_never_queries_or_returns_social_content_and_auth_secrets(m3_backup_account):
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = collect()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert statements and all(statement.lstrip().startswith("select") for statement in statements)
    joined = "\n".join(statements)
    for secret in ("password_hash", "token_hash", "csrf_hash", "ciphertext", "input_messages", "request_hash", "select *"):
        assert secret not in joined
    assert "chat_messages" in joined and "sender_id,seq" in joined
    assert "body" not in joined
    assert "social_profiles" not in joined and "realtime_tickets" not in joined
    assert "social_archive_records" in joined and "payload from public.social_archive_records" not in joined
    encoded = repr(result)
    for private in ("PROFILE-PRIVATE-TEXT", "CHAT-PRIVATE-MESSAGE", "COMMUNITY-PRIVATE-DRAFT", "ARCHIVE-PRIVATE-CONTENT", "SESSION-SECRET-HASH", "CSRF-SECRET-HASH", "REALTIME-SECRET-HASH", "WORKER-SECRET-TOKEN"):
        assert private not in encoded


@pytest.mark.parametrize("kind", ["diagnostic", "semantic.build", "learning.reminder"])
def test_explicit_supported_new_job_kinds_remain_collectable(m3_backup_account, kind):
    a = m3_backup_account
    before = collect()
    with SessionLocal.begin() as db:
        db.add(Job(owner_id=a.owner, kind=kind, dedupe_key="m3-supported-" + kind,
            payload={} if kind != "diagnostic" else {"duration_seconds": 1}, status="queued"))
    after = collect()
    assert after["keys"] == before["keys"] and after["active_writers"] == before["active_writers"] + 1


def test_unknown_job_kind_still_fails_closed(m3_backup_account):
    with SessionLocal.begin() as db:
        db.add(Job(owner_id=m3_backup_account.owner, kind="future.unknown-writer", dedupe_key="m3-unknown", payload={}))
    with pytest.raises(RetentionError) as failure:
        collect()
    assert failure.value.code == "retention_reference_invalid"


@pytest.mark.parametrize("damage", ["wrong_job_snapshot", "foreign_chat_sender", "wrong_attachment_thread", "foreign_archive_key", "malformed_source_path"])
def test_damaged_or_cross_owner_m3_references_abort_entire_collection(m3_backup_account, damage):
    a = m3_backup_account
    with SessionLocal.begin() as db:
        if damage == "wrong_job_snapshot":
            job = db.get(Job, a.jobs[0])
            job.payload = {**job.payload, "snapshot_id": str(a.failed_snapshot)}
        elif damage == "foreign_chat_sender":
            row = db.scalar(select(ChatMessage))
            row.sender_id = a.third
        elif damage == "wrong_attachment_thread":
            # DB foreign keys do not prove sender participation; collector must.
            row = db.get(ChatAttachment, a.chat)
            row.owner_id = a.third
        elif damage == "foreign_archive_key":
            row = db.get(SocialArchiveAsset, a.offline_asset)
            row.object_key = db.get(ChatAttachment, a.chat).object_key
        else:
            row = db.get(CommunityAsset, a.failed_asset)
            row.source_object_key = "../escape/key"
    with pytest.raises(RetentionError) as failure:
        collect()
    assert failure.value.code == "retention_reference_invalid"


def test_failed_and_cancelled_snapshot_sources_remain_roots(m3_backup_account):
    a = m3_backup_account
    before = collect()
    with SessionLocal.begin() as db:
        snapshot = db.get(CommunitySnapshot, a.preparing_snapshot)
        snapshot.status = "cancelled"
        job = db.get(Job, a.jobs[0])
        job.status, job.worker_token, job.lease_until = "failed", None, None
    after = collect()
    assert after["keys"] == before["keys"]
    assert after["active_writers"] == 0
    assert after["fingerprint"] != before["fingerprint"]
