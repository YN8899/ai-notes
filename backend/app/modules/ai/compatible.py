"""Private administrator-owned OpenAI-compatible catalog; never accepts browser credentials."""
import hashlib
import json
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.shared import outbound
from app.shared.config import settings
from app.shared.errors import AppError


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=120)
    text: bool
    vision: bool
    tools: bool
    streaming: bool = True

    @field_validator("id", "name")
    @classmethod
    def visible(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("invalid_model_metadata")
        value.encode("utf-8")
        return value


class ProviderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    name: str = Field(min_length=1, max_length=80)
    base_url: SecretStr
    api_key: SecretStr
    enabled: bool = True
    models: list[ModelProfile] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_profile(self):
        raw = self.base_url.get_secret_value()
        outbound.validate_url(raw)
        if raw.endswith("/") or len({m.id for m in self.models}) != len(self.models):
            raise ValueError("invalid_provider_profile")
        key = self.api_key.get_secret_value()
        if key and (not 16 <= len(key) <= 512 or any(not 33 <= ord(c) <= 126 for c in key)):
            raise ValueError("invalid_provider_key")
        if any(ord(c) < 32 for c in self.name):
            raise ValueError("invalid_provider_name")
        return self


def profiles():
    """Fail closed on invalid deployment config; error never echoes secret JSON."""
    try:
        raw = settings().ai_provider_profiles.get_secret_value()
        payload = json.loads(raw)
        if not isinstance(payload, list) or len(payload) > 16:
            raise ValueError()
        custom = [ProviderProfile.model_validate(p) for p in payload]
        if len({p.id for p in custom}) != len(custom) or any(p.id == "deepseek" for p in custom):
            raise ValueError()
        builtin = ProviderProfile(id="deepseek", name="DeepSeek", base_url=SecretStr("https://api.deepseek.com"),
            api_key=settings().deepseek_api_key, models=[
                ModelProfile(id="deepseek-flash", name="DeepSeek Flash", text=True, vision=True, tools=True),
                ModelProfile(id="deepseek-v4-pro", name="DeepSeek V4 Pro", text=True, vision=False, tools=True),
            ])
        return [builtin, *custom]
    except Exception:
        raise AppError(503, "ai_catalog_invalid", "共享模型目录配置无效，请联系管理员") from None


@dataclass(frozen=True, repr=False)
class ResolvedModel:
    provider: str
    model: str
    capabilities: dict
    fingerprint: str
    endpoint: str = field(repr=False)
    secret: str = field(repr=False)


def resolve(provider, model, *, vision=False, tools=False, frozen=None):
    profile = next((p for p in profiles() if p.id == provider), None)
    selected = next((m for m in profile.models if m.id == model), None) if profile else None
    if profile is None or selected is None or not profile.enabled:
        raise AppError(422, "ai_model_unavailable", "所选服务商或模型当前不可用")
    capabilities = {k: getattr(selected, k) for k in ("text", "vision", "tools", "streaming")}
    if vision and not selected.vision:
        raise AppError(422, "vision_model_required", "所选模型不支持图片或 PDF 页图")
    if not selected.text or not selected.streaming or tools and not selected.tools:
        raise AppError(422, "ai_capability_required", "所选模型不支持本次输入或工具能力")
    endpoint = profile.base_url.get_secret_value() + "/chat/completions"
    fingerprint = hashlib.sha256(json.dumps({"provider": provider, "model": model,
        "endpoint": endpoint, "capabilities": capabilities}, sort_keys=True).encode()).hexdigest()
    if frozen and (frozen.get("fingerprint") != fingerprint or frozen.get("capabilities") != capabilities):
        raise AppError(409, "ai_provider_changed", "排队后模型配置已变化，请重新确认后发送")
    key = profile.api_key.get_secret_value()
    if not key:
        raise AppError(503, "ai_not_configured", "共享模型服务尚未配置，请联系管理员")
    return ResolvedModel(provider, model, capabilities, fingerprint, endpoint, key)


def freeze(resolved):
    return {"fingerprint": resolved.fingerprint, "capabilities": resolved.capabilities}


def for_run(row):
    return resolve(row.provider, row.model, vision=bool(row.source and row.source.get("kind", "pdf") != "text"),
                   tools=bool(row.tools), frozen=row.provider_snapshot)


def public_catalog():
    from app.modules.ai.schemas import AiModelOut, AiProviderOut
    return [AiProviderOut(id=p.id, name=p.name, enabled=p.enabled and bool(p.api_key.get_secret_value()), models=[
        AiModelOut(id=m.id, name=m.name, supports_text=m.text, supports_vision=m.vision,
            supports_tools=m.tools, supports_streaming=m.streaming, supports_embedding=False) for m in p.models])
        for p in profiles()]
