from uuid import UUID

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app.modules.ai import providers, schemas as s, service
from app.modules.ai.models import AiCredential
from app.shared.config import settings
from app.shared.dependencies import CurrentUser, DB, current_user

router = APIRouter(tags=["ai"])


@router.get("/ai/providers", response_model=s.AiCatalogOut)
def provider_catalog(user: CurrentUser):
    return s.AiCatalogOut(storage_ready=bool(settings().ai_credential_encryption_key.get_secret_value()),
                          providers=providers.catalog())


@router.get("/ai/credentials", response_model=list[s.AiCredentialOut])
def credentials(db: DB, user: CurrentUser):
    rows = db.scalars(select(AiCredential).where(AiCredential.owner_id == user.id).order_by(AiCredential.provider))
    return [service.output(row) for row in rows]


@router.put("/ai/credentials/{provider}", response_model=s.AiCredentialOut)
def save_credential(provider: str, data: s.AiCredentialWrite, request: Request, db: DB, user: CurrentUser):
    credential = service.save(db, user, provider, data, request.state.request_id)
    db.commit()
    return service.output(credential)


@router.delete("/ai/credentials/{credential_id}", status_code=204)
def delete_credential(credential_id: UUID, data: s.AiCredentialRevision, request: Request, db: DB, user: CurrentUser):
    service.remove(db, user, credential_id, data.revision, request.state.request_id)
    db.commit()
    return Response(status_code=204)


@router.post("/ai/credentials/{credential_id}/test", response_model=s.AiCredentialOut)
def test_credential(credential_id: UUID, data: s.AiCredentialRevision, request: Request, db: DB, user: CurrentUser):
    provider, secret, started_at = service.start_test(db, user, credential_id, data.revision)
    # Release account/credential locks before waiting for the provider. No automatic retry.
    db.commit()
    result = providers.check_key(provider, secret)
    del secret
    # Logout, disabling, replacement or deletion during the request invalidates its writeback.
    db.expire_all()
    user = current_user(request, db)
    credential = service.finish_test(db, user, credential_id, data.revision, started_at, result, request.state.request_id)
    db.commit()
    return service.output(credential)
