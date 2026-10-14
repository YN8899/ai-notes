from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, update, and_, or_, delete
from sqlalchemy.dialects.postgresql import insert
from app.shared.db import SessionLocal, utcnow
from app.shared.models import Job, Outbox, Notification, RateBucket, AuthSession, ResetToken
from app.shared.config import settings
from app.shared.errors import AppError


def enqueue(db, owner_id, data):
    payload = {"duration_seconds": data.duration_seconds}
    stmt = insert(Job).values(id=uuid4(), owner_id=owner_id, kind="diagnostic", dedupe_key=data.request_key, payload=payload, status="queued", attempts=0, max_attempts=3, run_after=utcnow(), created_at=utcnow()).on_conflict_do_nothing(constraint="job_dedupe").returning(Job.id)
    job_id = db.scalar(stmt)
    job = db.get(Job, job_id) if job_id else db.scalar(select(Job).where(Job.owner_id == owner_id, Job.dedupe_key == data.request_key))
    if job.kind != "diagnostic" or job.payload != payload:
        raise AppError(409, "request_key_conflict", "同一个请求标识不能用于不同任务")
    return job


def claim():
    now = utcnow()
    with SessionLocal.begin() as db:
        # Exhausted leases are terminal; they must not be claimed forever.
        db.execute(update(Job).where(Job.status == "running", Job.lease_until <= now, Job.attempts >= Job.max_attempts).values(status="failed", worker_token=None, lease_until=None, error_code="lease_exhausted"))
        stmt = select(Job).where(or_(and_(Job.status == "queued", Job.run_after <= now), and_(Job.status == "running", Job.lease_until <= now)), Job.attempts < Job.max_attempts).order_by(Job.run_after, Job.id).with_for_update(skip_locked=True).limit(1)
        job = db.scalar(stmt)
        if not job:
            return None
        job.status = "running"
        job.attempts += 1
        job.worker_token = str(uuid4())
        job.lease_until = now + timedelta(seconds=settings().job_lease_seconds)
        db.flush()
        return {"id": job.id, "token": job.worker_token, "kind": job.kind, "payload": job.payload}


def heartbeat(job_id, worker_token):
    now = utcnow()
    with SessionLocal.begin() as db:
        result = db.execute(update(Job).where(Job.id == job_id, Job.status == "running", Job.worker_token == worker_token, Job.lease_until > now).values(lease_until=now + timedelta(seconds=settings().job_lease_seconds)))
        return result.rowcount == 1


def finish(job_id, worker_token, result):
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job or job.status != "running" or job.worker_token != worker_token or job.lease_until <= utcnow():
            return False
        # Side effect and outbox creation share the lease-checked transaction.
        job.status, job.result = "succeeded", result
        job.error_code, job.worker_token, job.lease_until = None, None, None
        db.execute(insert(Outbox).values(id=uuid4(), user_id=job.owner_id, job_id=job.id, kind="job.succeeded", payload={"message": "后台自检已完成"}, created_at=utcnow()).on_conflict_do_nothing(index_elements=[Outbox.job_id]))
        return True


def fail(job_id, worker_token, code="handler_failed"):
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job or job.status != "running" or job.worker_token != worker_token or job.lease_until <= utcnow():
            return False
        job.status = "failed" if job.attempts >= job.max_attempts else "queued"
        job.run_after = utcnow() + timedelta(seconds=min(300, 2 ** job.attempts))
        job.error_code, job.worker_token, job.lease_until = code, None, None
        return True


def deliver_outbox():
    delivered = 0
    with SessionLocal.begin() as db:
        events = db.scalars(select(Outbox).where(Outbox.delivered_at.is_(None)).order_by(Outbox.created_at, Outbox.id).with_for_update(skip_locked=True).limit(50)).all()
        for event in events:
            db.execute(insert(Notification).values(id=uuid4(), user_id=event.user_id, event_id=event.id, message=event.payload["message"], created_at=utcnow()).on_conflict_do_nothing(index_elements=[Notification.event_id]))
            event.delivered_at = utcnow()
            delivered += 1
    return delivered


def maintenance():
    now = utcnow()
    with SessionLocal.begin() as db:
        db.execute(delete(RateBucket).where(RateBucket.expires_at < now - timedelta(days=1)))
        db.execute(delete(AuthSession).where(AuthSession.expires_at < now - timedelta(days=7)))
        db.execute(delete(ResetToken).where(ResetToken.expires_at < now - timedelta(days=7)))
