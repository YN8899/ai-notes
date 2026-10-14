from uuid import UUID
from fastapi import APIRouter, Request, Query
from sqlalchemy import select
from app.shared.dependencies import DB, CurrentUser, Admin
from app.shared.models import Job, Notification
from app.shared.permissions import require_owner, audit
from app.shared.errors import AppError
from app.shared.db import utcnow
from app.modules.jobs import service, schemas as s

router = APIRouter()


@router.post("/jobs/diagnostic", response_model=s.JobOut, status_code=202)
def diagnostic(data: s.DiagnosticIn, request: Request, db: DB, user: CurrentUser):
    # Bound persistent queue growth, even though payloads contain no user data.
    active = db.scalars(select(Job.id).where(Job.owner_id == user.id, Job.status.in_(["queued", "running"]))).all()
    existing = db.scalar(select(Job).where(Job.owner_id == user.id, Job.dedupe_key == data.request_key))
    if len(active) >= 10 and not existing:
        raise AppError(429, "queue_full", "在途任务已达上限，请稍后再试")
    job = service.enqueue(db, user.id, data)
    audit(db, user.id, "job.enqueue", request.state.request_id, job.id)
    db.commit()
    return job


@router.get("/jobs", response_model=list[s.JobOut])
def jobs(db: DB, user: CurrentUser, limit: int = Query(30, ge=1, le=100)):
    return db.scalars(select(Job).where(Job.owner_id == user.id).order_by(Job.created_at.desc(), Job.id).limit(limit)).all()


@router.get("/jobs/{job_id}", response_model=s.JobOut)
def get_job(job_id: UUID, db: DB, user: CurrentUser):
    return require_owner(user, db.get(Job, job_id))


@router.post("/admin/jobs/{job_id}/retry", response_model=s.JobOut)
def retry(job_id: UUID, request: Request, db: DB, admin: Admin):
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job:
        raise AppError(404, "not_found", "任务不存在")
    if job.status != "failed":
        raise AppError(409, "retry_unavailable", "仅失败任务可以重试")
    job.status, job.attempts, job.run_after = "queued", 0, utcnow()
    job.worker_token, job.lease_until, job.error_code = None, None, None
    audit(db, admin.id, "job.retry", request.state.request_id, job.id)
    db.commit()
    return job


@router.get("/admin/jobs", response_model=list[s.JobOut])
def admin_jobs(db: DB, admin: Admin, limit: int = Query(50, ge=1, le=100)):
    # Operational metadata only: JobOut intentionally omits payloads and owner content.
    return db.scalars(select(Job).order_by(Job.created_at.desc(), Job.id).limit(limit)).all()


@router.get("/notifications", response_model=list[s.NotificationOut])
def notifications(db: DB, user: CurrentUser, limit: int = Query(30, ge=1, le=100)):
    return db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc(), Notification.id).limit(limit)).all()


@router.post("/notifications/{notification_id}/read", response_model=s.NotificationOut)
def read_notification(notification_id: UUID, db: DB, user: CurrentUser):
    n = db.scalar(select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id).with_for_update())
    if not n:
        raise AppError(404, "not_found", "通知不存在")
    n.read_at = n.read_at or utcnow()
    db.commit()
    return n
