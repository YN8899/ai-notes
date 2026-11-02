import base64
import re
from uuid import UUID
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from app.shared.outbound import validate_url


def decode_key(value: str, length: int) -> bytes:
    if len(value) != (length * 8 + 5) // 6 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid_push_key")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception:
        raise ValueError("invalid_push_key") from None
    if len(raw) != length or base64.urlsafe_b64encode(raw).decode().rstrip("=") != value:
        raise ValueError("invalid_push_key")
    return raw


def public_key(value: str) -> str:
    raw = decode_key(value, 65)
    if raw[0] != 4:
        raise ValueError("invalid_push_key")
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    return value


class PushKeys(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    p256dh: SecretStr
    auth: SecretStr

    @field_validator("p256dh")
    @classmethod
    def p256(cls, value):
        public_key(value.get_secret_value())
        return value

    @field_validator("auth")
    @classmethod
    def auth_key(cls, value):
        decode_key(value.get_secret_value(), 16)
        return value


class PushIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    endpoint: SecretStr
    keys: PushKeys
    expirationTime: int | None = Field(default=None, ge=0)

    @field_validator("endpoint")
    @classmethod
    def endpoint_url(cls, value):
        validate_url(value.get_secret_value())
        return value


class PushConfigOut(BaseModel):
    enabled: bool
    publicVapid: str | None


class PushStatusOut(BaseModel):
    id: UUID
    enabled: bool


class PushTestIn(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    request_key: UUID
    subscription_id: UUID
