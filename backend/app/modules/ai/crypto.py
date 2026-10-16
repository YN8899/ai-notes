"""Versioned authenticated encryption; the master secret never enters the database."""
import base64
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.shared.config import settings
from app.shared.errors import AppError

KEY_VERSION = 1


def _cipher():
    value = settings().ai_credential_encryption_key.get_secret_value()
    if not value:
        raise AppError(503, "ai_storage_unavailable", "模型凭据存储尚未配置，请联系管理员")
    key = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    return AESGCM(key)


def _context(owner_id, provider, credential_id, version):
    return f"ai-credential:{version}:{owner_id}:{provider}:{credential_id}".encode()


def encrypt_key(value, owner_id, provider, credential_id):
    nonce = secrets.token_bytes(12)
    return nonce + _cipher().encrypt(nonce, value.encode(), _context(owner_id, provider, credential_id, KEY_VERSION))


def decrypt_key(ciphertext, owner_id, provider, credential_id, version):
    if version != KEY_VERSION:
        raise AppError(503, "ai_credential_unavailable", "模型凭据暂不可用，请重新保存 Key 或联系管理员")
    try:
        return _cipher().decrypt(ciphertext[:12], ciphertext[12:], _context(owner_id, provider, credential_id, version)).decode()
    except (InvalidTag, ValueError, UnicodeError):
        raise AppError(503, "ai_credential_unavailable", "模型凭据暂不可用，请重新保存 Key 或联系管理员") from None
