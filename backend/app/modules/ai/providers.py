"""Controlled DeepSeek discovery only: no chat generation, redirects or custom hosts."""
from dataclasses import dataclass
import json

import httpx

from app.modules.ai.schemas import AiModelOut, AiProviderOut

MODELS_URL = "https://api.deepseek.com/models"
MODELS = (
    AiModelOut(id="deepseek-flash", name="DeepSeek Flash", supports_text=True,
               supports_vision=True, supports_streaming=True, supports_embedding=False),
    AiModelOut(id="deepseek-v4-pro", name="DeepSeek V4 Pro", supports_text=True,
               supports_vision=False, supports_streaming=True, supports_embedding=False),
)


def catalog():
    return [AiProviderOut(id="deepseek", name="DeepSeek", models=list(MODELS))]


@dataclass(frozen=True)
class CredentialCheck:
    status: str
    code: str
    models: tuple[str, ...] = ()


def create_client():
    return httpx.Client(timeout=httpx.Timeout(10, connect=5), follow_redirects=False, trust_env=False)


def check_key(provider, secret):
    if provider != "deepseek":
        return CredentialCheck("unavailable", "provider_unsupported")
    try:
        with create_client() as client:
            with client.stream("GET", MODELS_URL, headers={"Authorization": "Bearer " + secret,
                                                         "Accept": "application/json"}) as response:
                if response.status_code in {401, 403}:
                    return CredentialCheck("invalid", "credential_rejected")
                if response.status_code == 429:
                    return CredentialCheck("unavailable", "provider_rate_limited")
                if response.status_code != 200:
                    return CredentialCheck("unavailable", "provider_unavailable")
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    content.extend(chunk)
                    if len(content) > 65536:
                        return CredentialCheck("unavailable", "provider_invalid_response")
        data = json.loads(content)
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            return CredentialCheck("unavailable", "provider_invalid_response")
        available = {item["id"] for item in data["data"] if isinstance(item, dict) and isinstance(item.get("id"), str)}
        supported = tuple(model.id for model in MODELS if model.id in available)
        if not supported:
            return CredentialCheck("unavailable", "models_unavailable")
        return CredentialCheck("valid", "connected", supported)
    except httpx.HTTPError:
        return CredentialCheck("unavailable", "provider_unreachable")
    except (ValueError, UnicodeError):
        return CredentialCheck("unavailable", "provider_invalid_response")
