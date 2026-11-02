"""Copy frozen assets and publish only in a transaction holding the live lease."""
import hashlib
from uuid import UUID

from sqlalchemy import select

from app.modules.community.models import CommunityAsset, CommunityPost, CommunitySnapshot
from app.modules.files.oss_storage import StorageUnavailable
from app.modules.files.service import storage
from app.shared.db import SessionLocal, utcnow
from app.shared.models import Job, User


def lease_context(db, claimed):
    try:
        post_id = UUID(claimed["payload"]["post_id"])
        snapshot_id = UUID(claimed["payload"]["snapshot_id"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    post = db.scalar(select(CommunityPost).where(CommunityPost.id == post_id).with_for_update())
    job = db.scalar(select(Job).where(Job.id == claimed["id"]).with_for_update())
    if not job or job.kind != "community.publish" or job.status != "running" or job.worker_token != claimed["token"] or not job.lease_until or job.lease_until <= utcnow():
        return None
    snapshot = db.scalar(select(CommunitySnapshot).where(CommunitySnapshot.id == snapshot_id).with_for_update())
    if not post or not snapshot or job.payload != claimed["payload"] or snapshot.post_id != post.id or snapshot.owner_id != post.owner_id or job.owner_id != post.owner_id or snapshot.job_id != job.id:
        return None
    if snapshot.status != "preparing" or post.preparing_snapshot_id != snapshot.id or post.status == "hidden":
        job.status, job.error_code = "failed", "publication_cancelled"
        job.worker_token, job.lease_until = None, None
        return None
    owner = db.scalar(select(User.id).where(User.id == post.owner_id, User.status == "active"))
    if owner is None:
        snapshot.status, snapshot.error_code = "cancelled", "owner_unavailable"
        job.status, job.error_code = "failed", "publication_cancelled"
        job.worker_token, job.lease_until = None, None
        return None
    return post, snapshot, job


def record_failure(claimed):
    with SessionLocal.begin() as db:
        context = lease_context(db, claimed)
        if context is None:
            return
        _, snapshot, job = context
        snapshot.error_code = "copy_failed"
        if job.attempts >= job.max_attempts:
            snapshot.status = "failed"


def process_job(claimed):
    with SessionLocal.begin() as db:
        context = lease_context(db, claimed)
        if context is None:
            return False
        _, snapshot, _ = context
        rows = db.scalars(select(CommunityAsset).where(CommunityAsset.snapshot_id == snapshot.id).order_by(CommunityAsset.position)).all()
        assets = [{key: getattr(row, key) for key in ("id", "object_key", "original_object_key", "source_object_key", "source_original_object_key", "bytes", "original_bytes", "sha256", "original_sha256")} for row in rows]
    # Storage cannot join the SQL transaction. Deterministic destination keys
    # and frozen hashes make replays harmless; uncommitted copies remain private.
    try:
        adapter = storage() if assets else None
        for asset in assets:
            for source, target, expected_size, expected_hash in (
                (asset["source_object_key"], asset["object_key"], asset["bytes"], asset["sha256"]),
                (asset["source_original_object_key"], asset["original_object_key"], asset["original_bytes"], asset["original_sha256"]),
            ):
                # Always verify the destination on retry, and only read the
                # private source if a complete independent copy is missing.
                try:
                    data = adapter.read(target)
                    copied = len(data) == expected_size and hashlib.sha256(data).hexdigest() == expected_hash
                except (OSError, StorageUnavailable):
                    copied = False
                if not copied:
                    if not source or adapter.head(source).size != expected_size:
                        raise ValueError("publication_source_unavailable")
                    data = adapter.read(source)
                    if len(data) != expected_size or hashlib.sha256(data).hexdigest() != expected_hash:
                        raise ValueError("publication_source_changed")
                    adapter.put(target, data)
                    verified = adapter.read(target)
                    if len(verified) != expected_size or hashlib.sha256(verified).hexdigest() != expected_hash:
                        raise ValueError("publication_copy_mismatch")
    except (OSError, StorageUnavailable, ValueError):
        record_failure(claimed)
        raise
    with SessionLocal.begin() as db:
        context = lease_context(db, claimed)
        if context is None:
            return False
        post, snapshot, job = context
        rows = db.scalars(select(CommunityAsset).where(CommunityAsset.snapshot_id == snapshot.id).with_for_update()).all()
        for row in rows:
            row.status = "ready"
            row.source_object_key, row.source_original_object_key = None, None
        snapshot.status, snapshot.error_code = "ready", None
        post.live_snapshot_id, post.status = snapshot.id, "published"
        post.published_at, post.updated_at = utcnow(), utcnow()
        post.revision += 1
        job.status, job.result = "succeeded", {"post_id": str(post.id), "snapshot_id": str(snapshot.id), "version": snapshot.version}
        job.error_code, job.worker_token, job.lease_until = None, None, None
        return True
