from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app.modules.ai import crypto, schemas as s
from app.modules.ai.models import AiCredential
from app.shared.db import utcnow
from app.shared.errors import AppError
from app.shared.permissions import audit, require_owner


def output(credential):
    return s.AiCredentialOut(id=credential.id, provider=credential.provider,
                             masked_key="••••••••" + credential.key_suffix,
                             revision=credential.revision, created_at=credential.created_at,
                             updated_at=credential.updated_at, last_tested_at=credential.last_tested_at,
                             test_status=credential.test_status, test_code=credential.test_code,
                             available_models=credential.available_models)


def owned(db, user, credential_id, *, lock=False):
    query = select(AiCredential).where(AiCredential.id == credential_id, AiCredential.owner_id == user.id)
    if lock:
        query = query.with_for_update()
    return require_owner(user, db.scalar(query))


def check_revision(credential, revision):
    if credential.revision != revision:
        raise AppError(409, "ai_credential_conflict", "模型凭据已更新，请刷新后重试")


def save(db, user, provider, data, request_id):
    if provider != "deepseek":
        raise AppError(404, "ai_provider_unknown", "该模型服务商尚未开放")
    credential = db.scalar(select(AiCredential).where(AiCredential.owner_id == user.id,
                                                    AiCredential.provider == provider).with_for_update())
    creating = credential is None
    if creating:
        if data.revision is not None:
            raise AppError(409, "ai_credential_conflict", "模型凭据已更新，请刷新后重试")
        credential = AiCredential(id=uuid4(), owner_id=user.id, provider=provider, revision=1)
    else:
        check_revision(credential, data.revision)
    value = data.api_key.get_secret_value()
    encrypted = crypto.encrypt_key(value, user.id, provider, credential.id)
    if creating:
        db.add(credential)
    else:
        credential.revision += 1
    credential.ciphertext, credential.key_version = encrypted, crypto.KEY_VERSION
    credential.key_suffix, credential.updated_at = value[-4:], utcnow()
    credential.last_test_started_at = credential.last_tested_at = None
    credential.test_status = credential.test_code = None
    credential.available_models = []
    db.flush()
    audit(db, user.id, "ai.credential.created" if creating else "ai.credential.replaced",
          request_id, credential.id, {"provider": provider})
    return credential


def remove(db, user, credential_id, revision, request_id):
    credential = owned(db, user, credential_id, lock=True)
    check_revision(credential, revision)
    audit(db, user.id, "ai.credential.deleted", request_id, credential.id, {"provider": credential.provider})
    db.delete(credential)


def start_test(db, user, credential_id, revision):
    credential = owned(db, user, credential_id, lock=True)
    check_revision(credential, revision)
    if credential.last_test_started_at and credential.last_test_started_at > utcnow() - timedelta(seconds=10):
        raise AppError(429, "ai_test_rate_limited", "请稍后再测试连接")
    secret = crypto.decrypt_key(credential.ciphertext, user.id, credential.provider,
                                credential.id, credential.key_version)
    credential.last_test_started_at = utcnow()
    return credential.provider, secret, credential.last_test_started_at


def finish_test(db, user, credential_id, revision, started_at, result, request_id):
    credential = owned(db, user, credential_id, lock=True)
    check_revision(credential, revision)
    if credential.last_test_started_at != started_at:
        raise AppError(409, "ai_test_superseded", "已有较新的连接测试，请刷新查看结果")
    credential.last_tested_at = utcnow()
    credential.test_status, credential.test_code = result.status, result.code
    credential.available_models = list(result.models)
    audit(db, user.id, "ai.credential.tested", request_id, credential.id,
          {"provider": credential.provider, "code": result.code})
    return credential
