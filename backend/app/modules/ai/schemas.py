from typing import Literal
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    title: str
    created_at: datetime


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    previous_run_id: UUID | None = None
    model: Literal["deepseek-flash", "deepseek-v4-pro"] = "deepseek-flash"
    prompt: str = Field(min_length=1, max_length=8000)

    @field_validator("prompt")
    @classmethod
    def nonempty_prompt(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("Prompt must contain text without null characters")
        value.encode("utf-8")
        return value


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conversation_id: UUID
    request_key: UUID
    model: str
    prompt: str
    answer: str
    status: Literal["queued", "running", "succeeded", "cancelled", "interrupted", "failed", "truncated"]
    usage: dict[str, int] | None
    error_code: str | None
    created_at: datetime
    finished_at: datetime | None


class ConversationDetail(ConversationOut):
    runs: list[RunOut]
