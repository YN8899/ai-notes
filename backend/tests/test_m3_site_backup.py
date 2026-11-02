"""Real PostgreSQL full-schema M3 backups, independent objects and runtime reset."""
from datetime import timedelta
import hashlib
import io
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
import pytest
from sqlalchemy import func, select

from app.modules.community.models import CommunityAsset, CommunityComment, CommunityPost, CommunityReaction, CommunityReport, CommunitySnapshot
from app.modules.exports.social_models import SocialArchiveAsset, SocialArchiveRecord
from app.modules.files.storage import LocalStorage
from app.modules.notes.models import Note
from app.modules.social.chat_models import ChatAttachment, ChatMessage, ChatReadCursor, ChatThread, RealtimeTicket
from app.modules.social.models import Follow, Friendship, NoteGrant, SocialProfile
from app.shared.db import SessionLocal, engine, utcnow
from app.shared.models import AuthSession, Job, User
from test_site_backup import site_tool, site_support, postgres_container, site_target, _backup, _restore, _rows, _empty_target


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def m3_backup_account(tmp_path):
    """Three real owners, every M3 table, live/withdrawn/failed/preparing history."""
    store = LocalStorage(tmp_path / "private-source")
    contents = {}

    def put(key, data):
        store.put(key, data)
        contents[key] = data
        return key

    buffer = io.BytesIO()
    Image.new("RGB", (12, 7), "green").save(buffer, "JPEG")
    reading = buffer.getvalue()
    buffer = io.BytesIO()
    Image.new("RGB", (12, 7), "green").save(buffer, "PNG")
    original = buffer.getvalue()
    with SessionLocal.begin() as db:
        owner = db.scalar(select(User).where(User.username == "admin"))
        peers = [User(username=name, display_name=name, password_hash="synthetic-test-password-hash") for name in ("m3-peer", "m3-third")]
        db.add_all(peers)
        db.flush()
        peer, third = peers
        note = Note(owner_id=owner.id, title="授权的私人笔记", plain_text="私人正文绝不应进入collector输出")
        source = Note(owner_id=owner.id, title="可以物理移除的原始图片笔记", kind="image", deleted_at=utcnow())
        db.add_all([note, source])
        db.flush()
        low, high = sorted((owner.id, peer.id))
        db.add_all([SocialProfile(owner_id=owner.id, bio="私密简介", details="PROFILE-PRIVATE-TEXT", visibility="friends"),
            Friendship(user_low_id=low, user_high_id=high, requester_id=owner.id, status="accepted"),
            Follow(follower_id=peer.id, followee_id=owner.id),
            NoteGrant(owner_id=owner.id, note_id=note.id, grantee_id=peer.id)])
        thread = ChatThread(user_low_id=low, user_high_id=high, last_seq=2)
        db.add(thread)
        db.flush()
        incoming = ChatMessage(thread_id=thread.id, sender_id=peer.id, seq=1, client_message_id="backup-peer-1", body="CHAT-PRIVATE-MESSAGE", fingerprint="1" * 64)
        outgoing = ChatMessage(thread_id=thread.id, sender_id=owner.id, seq=2, client_message_id="backup-self-2", body="保存的历史回复", fingerprint="2" * 64)
        db.add_all([incoming, outgoing, ChatReadCursor(thread_id=thread.id, user_id=owner.id, last_read_seq=1)])
        db.flush()
        chat_data = b"durable-chat-attachment"
        chat = ChatAttachment(thread_id=thread.id, owner_id=peer.id, message_id=incoming.id,
            object_key=put(f"chat/{peer.id}/{uuid4()}/original", chat_data), filename="聊天附件.txt", mime="text/plain",
            bytes=len(chat_data), sha256=digest(chat_data), request_key="backup-chat-file")
        db.add(chat)
        session = AuthSession(user_id=owner.id, token_hash="SESSION-SECRET-HASH", csrf_hash="CSRF-SECRET-HASH", expires_at=utcnow() + timedelta(hours=1))
        db.add(session)
        db.flush()
        ticket = RealtimeTicket(user_id=owner.id, session_id=session.id, token_hash="REALTIME-SECRET-HASH", expires_at=utcnow() + timedelta(minutes=1))
        db.add(ticket)
        post_ids, ready_assets, pending_assets, snapshot_ids, job_ids = [], [], [], [], []
        for index, state in enumerate(("published", "withdrawn", "hidden", "draft")):
            post = CommunityPost(owner_id=owner.id, title=f"帖子{index}", body_json={}, plain_text="COMMUNITY-PRIVATE-DRAFT",
                subject="数学", post_type="note", tags=["历史"], source_note_id=source.id,
                source_revision=1, status=state, published_at=utcnow() if state != "draft" else None)
            db.add(post)
            db.flush()
            snapshot_state = "ready" if state != "draft" else "preparing"
            snapshot = CommunitySnapshot(owner_id=owner.id, post_id=post.id, version=1, status=snapshot_state,
                title=post.title, body_json={}, plain_text="独立快照正文", subject="数学", post_type="note", tags=["历史"],
                source_revision=1, request_key=uuid4(), request_hash="3" * 64)
            db.add(snapshot)
            db.flush()
            prefix = f"community/{snapshot.id}/{uuid4()}"
            fields = {"owner_id": owner.id, "snapshot_id": snapshot.id, "position": 0, "filename": "独立图片.png", "mime": "image/png",
                "width": 12, "height": 7, "object_key": prefix + "/reading.jpg", "original_object_key": prefix + "/original",
                "bytes": len(reading), "original_bytes": len(original), "sha256": digest(reading), "original_sha256": digest(original),
                "status": "ready" if snapshot_state == "ready" else "pending"}
            if snapshot_state == "ready":
                put(fields["object_key"], reading)
                put(fields["original_object_key"], original)
                post.live_snapshot_id = snapshot.id
            else:
                fields["source_object_key"] = put(f"frozen/{owner.id}/{uuid4()}/reading.jpg", reading)
                fields["source_original_object_key"] = put(f"frozen/{owner.id}/{uuid4()}/original", original)
                job = Job(owner_id=owner.id, kind="community.publish", dedupe_key="backup-prepare",
                    payload={"post_id": str(post.id), "snapshot_id": str(snapshot.id)}, status="running", attempts=1,
                    worker_token="WORKER-SECRET-TOKEN", lease_until=utcnow() + timedelta(minutes=1))
                db.add(job)
                db.flush()
                snapshot.job_id = job.id
                job_ids.append(job.id)
                post.preparing_snapshot_id = snapshot.id
            asset = CommunityAsset(**fields)
            db.add(asset)
            db.flush()
            (ready_assets if snapshot_state == "ready" else pending_assets).append(asset.id)
            post_ids.append(post.id)
            snapshot_ids.append(snapshot.id)
        # A failed publication's frozen source is not registered as File or
        # FileVariant; it is the last reference and must not disappear on restore.
        failed = CommunitySnapshot(owner_id=owner.id, post_id=post_ids[-1], version=2, status="failed",
            title="失败发布的冻结资料", body_json={}, plain_text="人工创作仍保留", subject="数学", post_type="note", tags=[],
            source_revision=1, request_key=uuid4(), request_hash="4" * 64, error_code="copy_failed")
        db.add(failed)
        db.flush()
        prefix = f"community/{failed.id}/{uuid4()}"
        failed_asset = CommunityAsset(owner_id=owner.id, snapshot_id=failed.id, position=0, filename="失败来源.png", mime="image/png",
            width=12, height=7, object_key=prefix + "/reading.jpg", original_object_key=prefix + "/original",
            source_object_key=put(f"frozen/{owner.id}/{uuid4()}/reading.jpg", reading),
            source_original_object_key=put(f"frozen/{owner.id}/{uuid4()}/original", original),
            bytes=len(reading), original_bytes=len(original), sha256=digest(reading), original_sha256=digest(original), status="pending")
        db.add(failed_asset)
        db.add_all([CommunityComment(owner_id=peer.id, post_id=post_ids[0], body="评论历史", request_key=uuid4()),
            CommunityReaction(owner_id=third.id, post_id=post_ids[0], kind="bookmark"),
            CommunityReport(owner_id=peer.id, post_id=post_ids[2], reason="举报历史", request_key=uuid4())])
        archive = SocialArchiveRecord(owner_id=third.id, kind="chat_message", source_id=uuid4(), payload={"offline_only": True, "text": "ARCHIVE-PRIVATE-CONTENT"})
        db.add(archive)
        db.flush()
        offline_data = b"inert-offline-history-object"
        offline = SocialArchiveAsset(owner_id=third.id, record_id=archive.id, source_id=uuid4(), role="attachment",
            object_key=put(f"restored/{third.id}/{uuid4()}/attachment", offline_data), filename="离线历史.txt", mime="text/plain",
            bytes=len(offline_data), sha256=digest(offline_data))
        db.add(offline)
        db.flush()
        result = SimpleNamespace(owner=owner.id, outsider=peer.id, third=third.id, store=store, contents=contents,
            note=note.id, source_note=source.id, thread=thread.id, chat=chat.id, session=session.id,
            ticket=ticket.id, posts=post_ids, ready_assets=ready_assets, pending_assets=pending_assets,
            failed_asset=failed_asset.id, preparing_snapshot=snapshot_ids[-1], failed_snapshot=failed.id,
            jobs=job_ids, archive=archive.id, offline_asset=offline.id)
    result.original_rows = _rows(engine)
    return result


def test_real_66table_m3_roundtrip_preserves_multiuser_history_and_normalizes_runtime(
    site_tool, site_support, postgres_container, site_target, m3_backup_account, tmp_path,
):
    a = m3_backup_account
    path = tmp_path / "m3-site"
    before = a.original_rows
    result = _backup(site_tool, site_support, a, postgres_container, path)
    manifest = site_tool.verify_site_backup(path)
    assert set(manifest["tables"]) == set(before) | {"alembic_version"}
    assert len(manifest["tables"]) == len(site_tool.FROZEN_TABLE_NAMES[manifest["schema_revision"]])
    restored = _restore(site_tool, path, site_target)
    after = _rows(site_target.engine)
    assert len(after["users"]) == 3
    for table in before.keys() - {"sessions", "jobs", "realtime_tickets", "community_snapshots"}:
        assert after[table] == before[table], table
    with site_target.sessions() as db:
        assert db.get(AuthSession, a.session).revoked_at is not None
        assert db.scalar(select(func.count()).select_from(RealtimeTicket)) == 0
        snapshot = db.get(CommunitySnapshot, a.preparing_snapshot)
        assert (snapshot.status, snapshot.error_code) == ("failed", "site_restore_interrupted")
        assert db.get(CommunitySnapshot, a.failed_snapshot).error_code == "copy_failed"
        job = db.get(Job, a.jobs[0])
        assert (job.status, job.error_code, job.worker_token, job.lease_until) == ("failed", "site_restore_interrupted", None, None)
        assert db.get(CommunityPost, a.posts[1]).status == "withdrawn"
        assert db.get(CommunityPost, a.posts[2]).status == "hidden"
        assert db.get(ChatMessage, before["chat_messages"][0]["id"]).body == before["chat_messages"][0]["body"]
        assert db.get(SocialArchiveRecord, a.archive).payload["offline_only"] is True
        assert db.scalar(select(func.count()).select_from(NoteGrant)) == 1
    snapshots = {row["id"]: row for row in after["community_snapshots"]}
    for row in before["community_snapshots"]:
        current = snapshots[row["id"]]
        if row["status"] != "preparing":
            assert current == row
        else:
            assert {key: value for key, value in current.items() if key not in {"status", "error_code"}} == {
                key: value for key, value in row.items() if key not in {"status", "error_code"}}
    sessions = {row["id"]: row for row in after["sessions"]}
    for row in before["sessions"]:
        assert {key: value for key, value in sessions[row["id"]].items() if key != "revoked_at"} == {
            key: value for key, value in row.items() if key != "revoked_at"}
    contents = {p.relative_to(site_target.storage_root).as_posix(): p.read_bytes() for p in site_target.storage_root.rglob("*") if p.is_file()}
    assert contents == a.contents
    assert result["objects"] == restored["objects"] == len(a.contents)
    assert result["missing_optional_objects"] == 4
    assert restored["original_fingerprints_verified"] is True
    assert _rows(engine) == before


def test_pending_and_failed_frozen_sources_mandatory_but_unwritten_targets_optional(
    site_tool, site_support, postgres_container, m3_backup_account, tmp_path,
):
    a = m3_backup_account
    path = tmp_path / "m3-pending"
    _backup(site_tool, site_support, a, postgres_container, path)
    entries = {entry["key"]: entry for entry in site_tool.verify_site_backup(path)["objects"]}
    with SessionLocal() as db:
        for asset_id in [*a.pending_assets, a.failed_asset]:
            asset = db.get(CommunityAsset, asset_id)
            for field in ("source_object_key", "source_original_object_key"):
                assert entries[getattr(asset, field)]["mandatory"] is True
                assert entries[getattr(asset, field)]["status"] == "present"
            for field in ("object_key", "original_object_key"):
                assert entries[getattr(asset, field)]["mandatory"] is False
                assert entries[getattr(asset, field)]["status"] == "missing"


@pytest.mark.parametrize("kind", ["chat", "offline", "ready_reading", "ready_original", "failed_source"])
@pytest.mark.parametrize("damage", ["missing", "same_length_corrupt"])
def test_m3_missing_or_corrupt_mandatory_object_rejects_backup(
    site_tool, site_support, postgres_container, m3_backup_account, tmp_path, kind, damage,
):
    a = m3_backup_account
    with SessionLocal() as db:
        if kind == "chat":
            key = db.get(ChatAttachment, a.chat).object_key
        elif kind == "offline":
            key = db.get(SocialArchiveAsset, a.offline_asset).object_key
        else:
            row = db.get(CommunityAsset, a.failed_asset if kind == "failed_source" else a.ready_assets[0])
            key = row.source_object_key if kind == "failed_source" else row.object_key if kind == "ready_reading" else row.original_object_key
    if damage == "missing":
        a.store.delete(key)
    else:
        a.store.put(key, b"x" * len(a.store.read(key)))
    with pytest.raises(site_tool.SiteBackupError):
        _backup(site_tool, site_support, a, postgres_container, tmp_path / "invalid-m3")
