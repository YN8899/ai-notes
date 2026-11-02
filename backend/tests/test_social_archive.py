"""Real PG/objects: v4 history preserves content without reviving authority."""
import copy
import hashlib
import io
import json
import shutil
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import zipfile

import pytest
from PIL import Image
from sqlalchemy import func, select
from app.modules.exports import service as exports
from app.modules.exports.social_models import SocialArchiveRecord, SocialArchiveAsset
from app.modules.social.models import SocialProfile, Friendship, Follow, NoteGrant
from app.modules.social.chat_models import ChatThread, ChatMessage, ChatReadCursor, ChatAttachment, RealtimeTicket
from app.modules.community.models import CommunityPost, CommunitySnapshot, CommunityAsset, CommunityComment, CommunityReaction, CommunityReport
from app.modules.notes.models import Note
from app.shared.db import SessionLocal, utcnow
from app.shared.models import User, AuthSession, Job
from app.shared.errors import AppError
from test_exports import account as export_account
from test_archive_restore import restore_tool, restore_database, target, _restore, _metadata, _counts, _rewrite_archive


@pytest.fixture
def m3_account(export_account):
    a = export_account
    body = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "我创作的完整社区正文"}]}]}
    image = io.BytesIO()
    Image.new("RGB", (8, 5), "blue").save(image, format="PNG")
    original = image.getvalue()
    image = io.BytesIO()
    Image.new("RGB", (8, 5), "blue").save(image, format="JPEG")
    reading = image.getvalue()
    with SessionLocal.begin() as db:
        db.add(SocialProfile(owner_id=a.owner, bio="我的简介", subject="数学", details="私密详细资料", visibility="private"))
        low, high = sorted([a.owner, a.outsider])
        friendship = Friendship(user_low_id=low, user_high_id=high, requester_id=a.owner, status="accepted")
        db.add_all([friendship, Follow(follower_id=a.owner, followee_id=a.outsider)])
        foreign = db.scalar(select(Note).where(Note.owner_id == a.outsider))
        db.add_all([NoteGrant(owner_id=a.owner, note_id=a.text, grantee_id=a.outsider),
                    NoteGrant(owner_id=a.outsider, note_id=foreign.id, grantee_id=a.owner)])
        thread = ChatThread(user_low_id=low, user_high_id=high, last_seq=2)
        db.add(thread)
        db.flush()
        incoming = ChatMessage(thread_id=thread.id, sender_id=a.outsider, client_message_id="peer-1", seq=1,
                               body="好友发给我的完整消息", fingerprint="a" * 64)
        outgoing = ChatMessage(thread_id=thread.id, sender_id=a.owner, client_message_id="self-2", seq=2,
                               body="我发出的完整消息", fingerprint="b" * 64)
        db.add_all([incoming, outgoing, ChatReadCursor(thread_id=thread.id, user_id=a.owner, last_read_seq=1)])
        db.flush()
        attachment = ChatAttachment(thread_id=thread.id, owner_id=a.outsider, message_id=incoming.id,
            object_key=f"chat/{a.outsider}/{uuid4()}/original", filename="收到的附件.txt", mime="text/plain",
            bytes=len(b"received-attachment"), sha256=hashlib.sha256(b"received-attachment").hexdigest(), request_key="peer-attachment")
        pending_foreign = ChatAttachment(thread_id=thread.id, owner_id=a.outsider,
            object_key=f"chat/{a.outsider}/{uuid4()}/original", filename="unsent-peer-secret.txt", mime="text/plain",
            bytes=6, sha256=hashlib.sha256(b"SECRET").hexdigest(), request_key="pending-peer")
        pending_owned = ChatAttachment(thread_id=thread.id, owner_id=a.owner,
            object_key=f"chat/{a.owner}/{uuid4()}/original", filename="未发送附件.txt", mime="text/plain",
            bytes=len(b"my-pending-upload"), sha256=hashlib.sha256(b"my-pending-upload").hexdigest(), request_key="pending-owned")
        db.add_all([attachment, pending_foreign, pending_owned])
        a.store.put(attachment.object_key, b"received-attachment")
        a.store.put(pending_foreign.object_key, b"SECRET")
        a.store.put(pending_owned.object_key, b"my-pending-upload")
        post = CommunityPost(owner_id=a.owner, title="我的独立帖子", body_json=copy.deepcopy(body), plain_text="我创作的完整社区正文",
            subject="数学", post_type="note", tags=["积分"], source_note_id=a.text, source_revision=2, status="published")
        other = CommunityPost(owner_id=a.outsider, title="foreign-draft-must-not-export", body_json={}, plain_text="other-owner-secret")
        db.add_all([post, other])
        db.flush()
        snapshot = CommunitySnapshot(owner_id=a.owner, post_id=post.id, version=1, status="ready", title=post.title,
            body_json=copy.deepcopy(body), plain_text=post.plain_text, subject=post.subject, post_type=post.post_type,
            tags=post.tags, source_note_id=a.text, source_revision=2, request_key=uuid4(), request_hash="c" * 64)
        db.add(snapshot)
        db.flush()
        post.live_snapshot_id = snapshot.id
        post.published_at = utcnow()
        asset = CommunityAsset(owner_id=a.owner, snapshot_id=snapshot.id, position=0, filename="原图.png", mime="image/png",
            width=8, height=5, object_key=f"community/{a.owner}/{uuid4()}/reading", original_object_key=f"community/{a.owner}/{uuid4()}/original",
            bytes=len(reading), original_bytes=len(original), sha256=hashlib.sha256(reading).hexdigest(),
            original_sha256=hashlib.sha256(original).hexdigest(), status="ready")
        db.add(asset)
        a.store.put(asset.object_key, reading)
        a.store.put(asset.original_object_key, original)
        db.add_all([CommunityComment(owner_id=a.owner, post_id=other.id, body="我自己写的评论", request_key=uuid4()),
                    CommunityComment(owner_id=a.outsider, post_id=post.id, body="foreign-comment-not-owned", request_key=uuid4()),
                    CommunityReaction(owner_id=a.owner, post_id=other.id, kind="bookmark"),
                    CommunityReport(owner_id=a.owner, post_id=other.id, reason="我自己的举报", request_key=uuid4())])
        db.flush()
        return SimpleNamespace(**vars(a), thread_id=thread.id, post_id=post.id, snapshot_id=snapshot.id,
            attachment_id=attachment.id, copied_original=original, copied_reading=reading,
            community_keys=[asset.object_key, asset.original_object_key])


def archive(a, path):
    with exports.create_archive(a.owner, a.session) as stream, path.open("wb") as out:
        shutil.copyfileobj(stream, out)
    return _metadata(path)


def test_v4_all_social_kinds_and_assets_restore_as_private_inert_history(m3_account, restore_tool, target, tmp_path, monkeypatch):
    a = m3_account
    path = tmp_path / "social-v4.zip"
    manifest, collections = archive(a, path)
    assert manifest["version"] == 5 and len(collections) == 37
    records, assets = collections["social_archive_records"], collections["social_archive_assets"]
    assert {row["kind"] for row in records} == {"profile", "friendship", "follow", "note_grant", "chat_thread", "chat_message",
        "chat_attachment", "community_post", "community_snapshot", "community_comment", "community_reaction", "community_report"}
    assert len(assets) == 4 and all(row["payload"]["offline_only"] is True for row in records)
    frozen = next(row for row in records if row["kind"] == "community_snapshot")
    assert frozen["payload"]["source_note_id"] == str(a.text)
    assert frozen["payload"]["source_revision"] == 2
    text = json.dumps(records, ensure_ascii=False)
    assert str(a.outsider) not in text
    assert "archived_" in text and "已脱敏" in text
    for expected in ("好友发给我的完整消息", "我发出的完整消息", "我创作的完整社区正文", "我自己写的评论", "私密详细资料"):
        assert expected in text
    for forbidden in ("foreign-draft-must-not-export", "foreign-comment-not-owned", "other-owner-secret", "unsent-peer-secret", "object_key", "token_hash", "request_hash"):
        assert forbidden not in text
    assert restore_tool._load_verifier().verify_archive(path) == manifest["counts"]
    _restore(restore_tool, path, target)
    with target.sessions() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(SocialArchiveRecord)) == len(records)
        assert db.scalar(select(func.count()).select_from(SocialArchiveAsset)) == 4
        for model in (Friendship, Follow, NoteGrant, ChatThread, ChatMessage, ChatAttachment, SocialProfile, CommunityPost, CommunitySnapshot, CommunityAsset, RealtimeTicket):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        from app.modules.files.storage import LocalStorage
        store = LocalStorage(target.storage_root)
        contents = [store.read(row.object_key) for row in db.scalars(select(SocialArchiveAsset))]
        assert set(contents) == {b"received-attachment", b"my-pending-upload", a.copied_original, a.copied_reading}
        for row in db.scalars(select(SocialArchiveAsset)):
            assert row.object_key.startswith("restore_")
            assert hashlib.sha256(store.read(row.object_key)).hexdigest() == row.sha256


def test_reexport_after_restore_preserves_exact_history_and_attachment_bytes(m3_account, restore_tool, target, tmp_path, monkeypatch):
    # Frozen lineage is history, not a live source FK or a future read grant.
    # Removing the original reference must not discard the independent content.
    with SessionLocal.begin() as db:
        db.get(CommunityPost, m3_account.post_id).source_note_id = None
        db.get(Note, m3_account.text).deleted_at = utcnow()
    path = tmp_path / "source-v4.zip"
    manifest, before = archive(m3_account, path)
    frozen = next(row for row in before["social_archive_records"] if row["kind"] == "community_snapshot")
    assert frozen["payload"]["source_note_id"] == str(m3_account.text)
    assert frozen["payload"]["source_revision"] == 2
    _restore(restore_tool, path, target)
    with target.sessions.begin() as db:
        session = AuthSession(user_id=m3_account.owner, token_hash=uuid4().hex, csrf_hash=uuid4().hex,
                              expires_at=utcnow() + timedelta(hours=1))
        db.add(session)
        db.flush()
        session_id = session.id
    monkeypatch.setattr(exports, "SessionLocal", target.sessions)
    monkeypatch.setattr(exports.settings(), "storage_root", str(target.storage_root))
    restored = SimpleNamespace(owner=m3_account.owner, session=session_id)
    second = tmp_path / "reexport-v4.zip"
    new_manifest, after = archive(restored, second)
    for name in ("social_archive_records", "social_archive_assets"):
        assert sorted(before[name], key=lambda row: row["id"]) == sorted(after[name], key=lambda row: row["id"])
    with zipfile.ZipFile(path) as first, zipfile.ZipFile(second) as other:
        for row in before["social_archive_assets"]:
            assert first.read(row["path"]) == other.read(row["path"])
    assert restore_tool._load_verifier().verify_archive(second) == new_manifest["counts"]


@pytest.mark.parametrize("damage", ["active", "foreign_owner", "missing_asset", "hash"])
def test_malicious_social_history_is_rejected_before_target_writes(m3_account, restore_tool, target, tmp_path, damage):
    source = tmp_path / "source.zip"
    archive(m3_account, source)
    damaged = tmp_path / "damaged.zip"
    def change(manifest, collections):
        if damage == "active":
            collections["social_archive_records"][0]["payload"]["offline_only"] = False
        elif damage == "foreign_owner":
            collections["social_archive_assets"][0]["owner_id"] = str(m3_account.outsider)
        elif damage == "missing_asset":
            collections["social_archive_assets"].pop()
        else:
            collections["social_archive_assets"][0]["sha256"] = "0" * 64
    _rewrite_archive(source, damaged, change)
    with pytest.raises(restore_tool.RestoreError):
        _restore(restore_tool, damaged, target)
    assert not any(_counts(target).values())
    assert not list(target.storage_root.iterdir())


def test_export_waits_for_community_publication_and_keeps_row_budget(m3_account, monkeypatch):
    with SessionLocal.begin() as db:
        job = Job(owner_id=m3_account.owner, kind="community.publish", dedupe_key="pending-community", payload={})
        db.add(job)
        db.flush()
        job_id = job.id
    with pytest.raises(AppError) as failure:
        exports.create_archive(m3_account.owner, m3_account.session)
    assert failure.value.code == "export_busy"
    with SessionLocal.begin() as db:
        db.get(Job, job_id).status = "failed"
    monkeypatch.setattr(exports.settings(), "export_max_rows", 40)
    with pytest.raises(AppError) as failure:
        exports.create_archive(m3_account.owner, m3_account.session)
    assert failure.value.code == "export_too_large"
