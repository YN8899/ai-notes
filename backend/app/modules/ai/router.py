from fastapi import APIRouter, Request

from app.modules.ai import providers, schemas as s
from app.shared.config import settings
from app.shared.dependencies import Admin, CurrentUser, DB, current_user
from app.shared.errors import AppError
from app.shared.permissions import audit, require_admin

router = APIRouter(tags=["ai"])


@router.get("/ai/providers", response_model=s.AiCatalogOut)
def provider_catalog(user: CurrentUser):
    return s.AiCatalogOut(platform_enabled=bool(settings().deepseek_api_key.get_secret_value()),
                          providers=providers.catalog())


@router.post("/ai/connection-test", response_model=s.AiConnectionOut)
def test_connection(request: Request, db: DB, user: Admin):
    secret = settings().deepseek_api_key.get_secret_value()
    if not secret:
        raise AppError(503, "ai_not_configured", "共享模型服务尚未配置，请联系管理员")
    # Release the account lock while waiting. Never send the secret back to the browser.
    db.commit()
    result = providers.check_key("deepseek", secret)
    del secret
    db.expire_all()
    user = require_admin(current_user(request, db))
    audit(db, user.id, "ai.connection.tested", request.state.request_id,
          details={"provider": "deepseek", "code": result.code})
    db.commit()
    return s.AiConnectionOut(status=result.status, code=result.code, available_models=list(result.models))
