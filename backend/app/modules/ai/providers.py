"""Public metadata and bounded administrator connection checks."""
from dataclasses import dataclass
import json

from app.modules.ai import compatible
from app.shared import outbound

MODELS_URL = "https://api.deepseek.com/models"


def catalog():
    return compatible.public_catalog()


@dataclass(frozen=True)
class CredentialCheck:
    status: str
    code: str
    models: tuple[str, ...] = ()


def check_key(provider, secret=None, *, before_send=None):
    profile = next((p for p in compatible.profiles() if p.id == provider and p.enabled), None)
    if not profile:
        return CredentialCheck("unavailable", "provider_unsupported")
    key = secret if secret is not None else profile.api_key.get_secret_value()
    if not key:
        return CredentialCheck("unavailable", "ai_not_configured")
    try:
        status, content = outbound.get_json(profile.base_url.get_secret_value() + "/models",
            headers={"Authorization": "Bearer " + key, "Accept": "application/json"}, before_send=before_send)
        if status in {401, 403}:
            return CredentialCheck("invalid", "credential_rejected")
        if status != 200:
            return CredentialCheck("unavailable", "provider_rate_limited" if status == 429 else "provider_unavailable")
        data = json.loads(content)
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise ValueError()
        available = {item["id"] for item in data["data"] if isinstance(item, dict) and isinstance(item.get("id"), str)}
        supported = tuple(model.id for model in profile.models if model.id in available)
        return CredentialCheck("valid", "connected", supported) if supported else CredentialCheck("unavailable", "models_unavailable")
    except (ValueError, UnicodeError, TypeError):
        return CredentialCheck("unavailable", "provider_invalid_response")
    except outbound.EndpointUnavailable as error:
        return CredentialCheck("unavailable", "provider_invalid_response" if error.reason == "invalid_response" else "provider_unreachable")
    except Exception:
        return CredentialCheck("unavailable", "provider_unreachable")
