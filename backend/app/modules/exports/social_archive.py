"""Freeze the member's social content as portable, non-authorizing history."""
import hashlib
import json
from datetime import date, datetime
from uuid import UUID, NAMESPACE_URL, uuid5
from sqlalchemy import or_, select
from app.shared.errors import AppError
from app.shared.models import User
from app.modules.notes.models import Note
from app.modules.social.models import SocialProfile, Friendship, Follow, NoteGrant
from app.modules.social.chat_models import ChatThread, ChatMessage, ChatReadCursor, ChatAttachment
from app.modules.community.models import (CommunityPost, CommunitySnapshot, CommunityAsset, CommunityComment,
                                         CommunityReaction, CommunityReport)
from app.modules.exports.social_models import SocialArchiveRecord, SocialArchiveAsset

FIELDS = {
    SocialArchiveRecord: ("id", "created_at", "owner_id", "kind", "source_id", "payload"),
    SocialArchiveAsset: ("id", "created_at", "owner_id", "record_id", "source_id", "role", "filename", "mime", "bytes", "sha256"),
}


def _json(value):
    def default(item):
        if isinstance(item, UUID):
            return str(item)
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError("invalid_archive_value")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=default)


def collect(db, owner_id, collections, internal_keys, *, max_rows, max_metadata_bytes, checkpoint, storage):
    """Merge prior inert history with live snapshots without dropping old versions.

    Identity includes the payload digest. Re-exporting unchanged live history is
    stable; editing a restored profile cannot erase the earlier recovered copy.
    Objects are checked again by the enclosing archive writer after detachment.
    """
    records, assets = collections["social_archive_records"], collections["social_archive_assets"]
    seen = {str(row["id"]) for row in records}
    asset_ids = {str(row["id"]) for row in assets}
    count = sum(len(rows) for rows in collections.values())
    metadata = sum(len(_json(row).encode()) + 1 for rows in collections.values() for row in rows)
    objects = []

    def budget(row):
        nonlocal count, metadata
        count += 1
        metadata += len(_json(row).encode()) + 1
        if count > max_rows or metadata > max_metadata_bytes:
            raise AppError(413, "export_too_large", "数据超过本次导出上限")
        checkpoint()

    def rows(model, *conditions):
        values = db.scalars(select(model).where(*conditions).order_by(model.id).limit(max_rows + 1)).yield_per(100)
        for index, value in enumerate(values):
            if index >= max_rows:
                # Refuse oversized source sets instead of exporting a truncated
                # subset when some pending peer uploads are excluded below.
                raise AppError(413, "export_too_large", "数据超过本次导出上限")
            if index % 100 == 0:
                checkpoint()
            yield value

    peers = {}
    def peer(identity):
        identity = str(identity)
        if identity not in peers:
            user = db.get(User, UUID(identity))
            if not user:
                raise ValueError("archive_peer_missing")
            if user.id == owner_id:
                peers[identity] = {"id": identity, "username": user.username, "display_name": user.display_name}
            else:
                anonymous = uuid5(NAMESPACE_URL, f"ai-notes/archive-peer/{owner_id}/{identity}")
                peers[identity] = {"id": str(anonymous), "username": "archived_"+anonymous.hex[:12],
                                   "display_name": "历史成员 "+anonymous.hex[:6]+"（已脱敏）"}
        return dict(peers[identity])

    def record(kind, row, payload):
        payload = json.loads(_json(dict(offline_only=True, **payload)))
        digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        identity = uuid5(NAMESPACE_URL, f"ai-notes/archive/{owner_id}/{kind}/{row.id}/{digest}")
        result = dict(id=identity, created_at=row.created_at, owner_id=owner_id, kind=kind, source_id=row.id, payload=payload)
        if str(identity) not in seen:
            budget(result)
            records.append(result)
            seen.add(str(identity))
        return result

    def asset(parent, source, role, key, size, checksum, filename, mime):
        if not key or not isinstance(size, int) or not 0 < size <= 50 * 1024**2:
            raise ValueError("archive_asset_missing")
        identity = uuid5(NAMESPACE_URL, f"ai-notes/archive/asset/{parent['id']}/{source.id}/{role}")
        if str(identity) in asset_ids:
            return
        if checksum is None:
            content = storage.read(key)
            if len(content) != size:
                raise ValueError("archive_asset_size")
            checksum = hashlib.sha256(content).hexdigest()
        result = dict(id=identity, created_at=source.created_at, owner_id=owner_id, record_id=parent["id"],
                      source_id=source.id, role=role, filename=filename, mime=mime, bytes=size, sha256=checksum,
                      path=f"objects/{identity}/original")
        budget(result)
        assets.append(result)
        asset_ids.add(str(identity))
        internal_keys[(SocialArchiveAsset, identity)] = key

    # Previously restored history remains exact; all asset paths are regenerated.
    for item in assets:
        item["path"] = f"objects/{item['id']}/original"

    for row in rows(SocialProfile, SocialProfile.owner_id == owner_id):
        record("profile", row, {name: getattr(row, name) for name in ("bio", "subject", "details", "visibility", "revision")})
    for row in rows(Friendship, or_(Friendship.user_low_id == owner_id, Friendship.user_high_id == owner_id)):
        record("friendship", row, dict(peer=peer(row.user_high_id if row.user_low_id == owner_id else row.user_low_id),
            status=row.status, requester_is_self=row.requester_id == owner_id, updated_at=row.updated_at))
    for row in rows(Follow, Follow.follower_id == owner_id):
        record("follow", row, dict(peer=peer(row.followee_id)))
    for row in rows(NoteGrant, or_(NoteGrant.owner_id == owner_id, NoteGrant.grantee_id == owner_id)):
        record("note_grant", row, dict(direction="sent" if row.owner_id == owner_id else "received", note_id=row.note_id,
            peer=peer(row.grantee_id if row.owner_id == owner_id else row.owner_id), revoked_at=row.revoked_at))
    threads = {}
    for row in rows(ChatThread, or_(ChatThread.user_low_id == owner_id, ChatThread.user_high_id == owner_id)):
        threads[row.id] = row
        reads = {str(item.user_id): item.last_read_seq for item in rows(ChatReadCursor, ChatReadCursor.thread_id == row.id)}
        other = row.user_high_id if row.user_low_id == owner_id else row.user_low_id
        record("chat_thread", row, dict(peer=peer(other), last_seq=row.last_seq,
            last_read_seq=reads.get(str(owner_id), 0), peer_read_seq=reads.get(str(other), 0)))
    thread_ids = list(threads)
    messages = {}
    chat_assets = list(rows(ChatAttachment, ChatAttachment.thread_id.in_(thread_ids)))
    for row in rows(ChatMessage, ChatMessage.thread_id.in_(thread_ids)):
        messages[row.id] = record("chat_message", row, dict(thread_id=row.thread_id, seq=row.seq,
            sender=peer(row.sender_id), sender_is_self=row.sender_id == owner_id, body=row.body,
            client_message_id=row.client_message_id,
            attachment_ids=[item.id for item in chat_assets if item.message_id == row.id]))
    for row in chat_assets:
        if row.message_id is None and row.owner_id != owner_id:
            continue
        if row.message_id is not None:
            parent = messages.get(row.message_id)
            if parent is None:
                raise ValueError("archive_message_missing")
        else:
            parent = record("chat_attachment", row, dict(thread_id=row.thread_id, filename=row.filename, pending=True))
        asset(parent, row, "attachment", row.object_key, row.bytes, row.sha256, row.filename, row.mime)
    for row in rows(CommunityPost, CommunityPost.owner_id == owner_id):
        record("community_post", row, {name: getattr(row, name) for name in (
            "title", "body_json", "plain_text", "subject", "post_type", "tags", "source_note_id", "source_revision",
            "revision", "status", "published_at", "updated_at", "moderation_reason")})
    snapshots = {}
    community_assets = list(rows(CommunityAsset, CommunityAsset.owner_id == owner_id))
    for row in rows(CommunitySnapshot, CommunitySnapshot.owner_id == owner_id):
        payload = {name: getattr(row, name) for name in (
            "post_id", "version", "status", "title", "body_json", "plain_text", "subject", "post_type", "tags",
            "source_note_id", "source_revision", "error_code")}
        payload["pages"] = [dict(id=item.id, position=item.position, width=item.width, height=item.height,
                                 filename=item.filename, mime=item.mime) for item in sorted(
            (item for item in community_assets if item.snapshot_id == row.id), key=lambda item: item.position)]
        snapshots[row.id] = record("community_snapshot", row, payload)
    for row in community_assets:
        parent = snapshots.get(row.snapshot_id)
        if parent is None:
            raise ValueError("archive_snapshot_missing")
        asset(parent, row, "reading", row.object_key if row.status == "ready" else row.source_object_key,
            row.bytes, row.sha256, row.filename, "image/jpeg")
        asset(parent, row, "original", row.original_object_key if row.status == "ready" else row.source_original_object_key,
            row.original_bytes, row.original_sha256, row.filename, row.mime)
    for row in rows(CommunityComment, CommunityComment.owner_id == owner_id):
        record("community_comment", row, dict(post_id=row.post_id, body=row.body, deleted_at=row.deleted_at))
    for row in rows(CommunityReaction, CommunityReaction.owner_id == owner_id):
        record("community_reaction", row, dict(post_id=row.post_id, kind=row.kind))
    for row in rows(CommunityReport, CommunityReport.owner_id == owner_id):
        record("community_report", row, dict(post_id=row.post_id, reason=row.reason, status=row.status,
            resolution=row.resolution, resolved_at=row.resolved_at))
    for item in assets:
        key = internal_keys[(SocialArchiveAsset, item["id"])]
        objects.append((item["path"], key, item["bytes"], item["sha256"]))
    return objects
