from datetime import timedelta
from typing import Annotated
import secrets
from fastapi import Depends, Request
from sqlalchemy import select, update, func, case
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from app.shared.db import get_db, SessionLocal, utcnow
from app.shared.config import settings
from app.shared.models import AuthSession, User, RateBucket
from app.shared.security import digest
from app.shared.errors import AppError
from app.shared.permissions import require_admin

DB = Annotated[Session, Depends(get_db)]


def current_user(request: Request, db: DB):
    # An explicit tab credential must never fall back to the shared Cookie jar.
    authorization = request.headers.get("authorization")
    request.state.tab_session = authorization is not None
    if authorization is not None:
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise AppError(401, "login_required", "请先登录")
        raw = parts[1]
    else:
        raw = request.cookies.get("notes_session", "")
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == digest(raw), AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow()))
    if not session:
        raise AppError(401, "login_required", "请先登录")
    stmt = select(User).where(User.id == session.user_id)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        stmt = stmt.with_for_update()
    user = db.scalar(stmt)
    # Refresh after the user lock: password reset / disabling might have revoked it.
    db.refresh(session)
    if user.status != "active" or session.revoked_at or session.expires_at <= utcnow():
        raise AppError(401, "login_required", "会话已失效，请重新登录")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("x-csrf-token", "")
        if not secrets.compare_digest(digest(supplied), session.csrf_hash):
            raise AppError(403, "csrf_invalid", "请求验证失败，请刷新页面")
    request.state.auth_session = session
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def admin_user(user: CurrentUser):
    return require_admin(user)


Admin = Annotated[User, Depends(admin_user)]


def preauth_csrf(request: Request):
    cookie, header = request.cookies.get("notes_csrf", ""), request.headers.get("x-csrf-token", "")
    if not cookie or not secrets.compare_digest(cookie, header):
        raise AppError(403, "csrf_invalid", "请求验证失败，请刷新页面")


def tab_preauth_csrf(request: Request):
    # Tab login must not depend on a shared pre-login Cookie: simultaneous
    # logins in fresh tabs could otherwise overwrite each other's CSRF Cookie.
    # Same-origin + a mandatory custom header blocks cross-site simple requests;
    # CORS is deliberately not enabled for these APIs.
    if request.headers.get("origin") != settings().app_origin or request.headers.get("x-notes-client") != "tab":
        raise AppError(403, "csrf_invalid", "请求验证失败，请刷新页面")


def auth_rate_limit(request: Request):
    # Persist the counter separately so a failed login cannot roll it back.
    now, cfg = utcnow(), settings()
    key = digest("auth:" + (request.client.host if request.client else "unknown"))
    with SessionLocal.begin() as db:
        stmt = insert(RateBucket).values(key=key, count=1, expires_at=now + timedelta(seconds=cfg.rate_limit_seconds))
        expired = RateBucket.expires_at <= now
        stmt = stmt.on_conflict_do_update(index_elements=[RateBucket.key], set_={"count": case((expired, 1), else_=RateBucket.count + 1), "expires_at": case((expired, now + timedelta(seconds=cfg.rate_limit_seconds)), else_=RateBucket.expires_at)}).returning(RateBucket.count)
        count = db.scalar(stmt)
    if count > cfg.rate_limit_requests:
        raise AppError(429, "rate_limited", "请求过于频繁，请稍后再试")
