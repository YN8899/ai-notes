from uuid import UUID, uuid4
from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select, text
from app.shared.dependencies import DB, CurrentUser
from app.shared.errors import AppError
from app.shared.security import digest
from app.shared.db import utcnow
from .models import PushSubscription
from . import service, schemas as s
from app.modules.jobs.schemas import JobOut

router = APIRouter(prefix="/push", tags=["push"])


@router.post("/test", response_model=JobOut, status_code=202)
def test_notification(data: s.PushTestIn, request: Request, db: DB, user: CurrentUser):
    job = service.enqueue_test(db, user.id, request.state.auth_session.id, data)
    db.commit()
    return job


@router.get("/config", response_model=s.PushConfigOut)
def config(user: CurrentUser):
    key = service.vapid()
    return {"enabled": key is not None, "publicVapid": service.public_vapid() if key else None}


@router.get("/subscriptions", response_model=list[s.PushStatusOut])
def status(request: Request, db: DB, user: CurrentUser):
    rows = db.scalars(select(PushSubscription).where(PushSubscription.owner_id == user.id,
        PushSubscription.session_id == request.state.auth_session.id).order_by(PushSubscription.created_at, PushSubscription.id)).all()
    ready = service.vapid() is not None
    return [{"id": row.id, "enabled": row.enabled and ready and row.vapid_key_hash == digest(service.public_vapid())} for row in rows]


@router.put("/subscriptions", response_model=s.PushStatusOut)
def subscribe(data: s.PushIn, request: Request, db: DB, user: CurrentUser):
    if service.vapid() is None:
        raise AppError(503, "push_disabled", "浏览器推送尚未配置，站内提醒仍可使用")
    endpoint = data.endpoint.get_secret_value()
    service.validate_endpoint(endpoint)
    # Global registration lock bounds growth and serializes endpoint uniqueness;
    # auth's user lock also serializes same-account session mutations.
    db.execute(text("SELECT pg_advisory_xact_lock(1448503341)"))
    row = db.scalar(select(PushSubscription).where(PushSubscription.endpoint_hash == digest(endpoint)).with_for_update())
    if row and row.owner_id != user.id:
        raise AppError(409, "push_already_bound", "此浏览器推送已关联其他登录会话，请先在原会话取消推送")
    if row and row.session_id != request.state.auth_session.id:
        # Explicit enable may recover this SAME account's device after its old
        # tab was closed. A fresh ID fences old-tab DELETEs and queued jobs.
        session_count = db.scalar(select(func.count()).select_from(PushSubscription).where(PushSubscription.session_id == request.state.auth_session.id))
        if session_count >= 3:
            raise AppError(429, "push_limit", "已达设备推送数量上限，请先取消旧订阅")
        row.session_id, row.id, row.created_at = request.state.auth_session.id, uuid4(), utcnow()
    if not row:
        session_count = db.scalar(select(func.count()).select_from(PushSubscription).where(PushSubscription.session_id == request.state.auth_session.id))
        owner_count = db.scalar(select(func.count()).select_from(PushSubscription).where(PushSubscription.owner_id == user.id))
        total = db.scalar(select(func.count()).select_from(PushSubscription))
        if session_count >= 3 or owner_count >= 32 or total >= 100_000:
            raise AppError(429, "push_limit", "已达设备推送数量上限，请先取消旧订阅")
        row = PushSubscription(owner_id=user.id, session_id=request.state.auth_session.id,
            endpoint_hash=digest(endpoint), endpoint=endpoint)
        db.add(row)
    row.p256dh, row.auth = data.keys.p256dh.get_secret_value(), data.keys.auth.get_secret_value()
    row.vapid_key_hash, row.enabled = digest(service.public_vapid()), True
    db.commit()
    return {"id": row.id, "enabled": True}


@router.delete("/subscriptions/{subscription_id}", status_code=204)
def unsubscribe(subscription_id: UUID, request: Request, db: DB, user: CurrentUser):
    row = db.scalar(select(PushSubscription).where(PushSubscription.id == subscription_id,
        PushSubscription.owner_id == user.id, PushSubscription.session_id == request.state.auth_session.id).with_for_update())
    if not row:
        raise AppError(404, "not_found", "订阅不存在")
    # Delete permits a fresh browser subscription after logout/account switching.
    db.delete(row)
    db.commit()
    return Response(status_code=204)
