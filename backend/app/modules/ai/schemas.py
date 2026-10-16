from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


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
    storage_ready: bool
    platform_enabled: Literal[False] = False
    providers: list[AiProviderOut]


class AiCredentialWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    api_key: SecretStr = Field(min_length=16, max_length=512)
    revision: int | None = Field(default=None, ge=1)

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value):
        if any(ord(character) < 33 or ord(character) > 126 for character in value.get_secret_value()):
            raise ValueError("API Key must contain only visible ASCII characters")
        return value


class AiCredentialRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


class AiCredentialOut(BaseModel):
    id: UUID
    provider: str
    masked_key: str
    revision: int
    created_at: datetime
    updated_at: datetime
    last_tested_at: datetime | None
    test_status: Literal["valid", "invalid", "unavailable"] | None
    test_code: str | None
    available_models: list[str]
