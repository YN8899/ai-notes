from fastapi import APIRouter, Request
from sqlalchemy import select

from app.modules.ai import providers, schemas as s, compatible
from app.shared.dependencies import Admin, CurrentUser, DB, current_user
from app.shared.errors import AppError
from app.shared.permissions import audit, require_admin
from app.shared.db import SessionLocal, utcnow
from app.shared.models import User, AuthSession

router = APIRouter(tags=["ai"])


@router.get("/ai/providers", response_model=s.AiCatalogOut)
def provider_catalog(user: CurrentUser):
    catalog = providers.catalog()
    return s.AiCatalogOut(platform_enabled=any(p.enabled for p in catalog), providers=catalog)


@router.post("/ai/connection-test", response_model=s.AiConnectionOut)
def test_connection(request: Request, db: DB, user: Admin, provider: str = "deepseek"):
    profile = next((p for p in compatible.profiles() if p.id == provider and p.enabled), None)
    if not profile or not profile.api_key.get_secret_value():
        raise AppError(503, "ai_not_configured", "共享模型服务尚未配置，请联系管理员")
    owner, session_id = user.id, request.state.auth_session.id
    def gate():
        with SessionLocal() as check:
            account, session = check.get(User, owner), check.get(AuthSession, session_id)
            return bool(account and account.status == "active" and account.role == "admin" and session
                and session.user_id == owner and not session.revoked_at and session.expires_at > utcnow())
    db.commit()
    result = providers.check_key(provider, before_send=gate)
    db.expire_all()
    user = require_admin(current_user(request, db))
    audit(db, user.id, "ai.connection.tested", request.state.request_id,
          details={"provider": provider, "code": result.code})
    db.commit()
    return s.AiConnectionOut(status=result.status, code=result.code, available_models=list(result.models))
