from typing import Literal

from pydantic import BaseModel


class AiModelOut(BaseModel):
    id: str
    name: str
    supports_text: bool
    supports_vision: bool
    supports_streaming: bool
    supports_embedding: bool


class AiProviderOut(BaseModel):
    id: str
    name: str
    models: list[AiModelOut]


class AiCatalogOut(BaseModel):
    credential_source: Literal["shared"] = "shared"
    platform_enabled: bool
    providers: list[AiProviderOut]


class AiConnectionOut(BaseModel):
    status: Literal["valid", "invalid", "unavailable"]
    code: str
    available_models: list[str]
