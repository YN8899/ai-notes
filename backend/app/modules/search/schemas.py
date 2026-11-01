from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.modules.ai.image_schemas import ImageInput, ImageRegion


class OcrCreate(ImageInput):
    request_key: UUID


class OcrUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(strict=True, ge=1)
    corrected_text: str = Field(max_length=64000)
    reviewed: bool = Field(strict=True)

    @field_validator("corrected_text")
    @classmethod
    def valid_text(cls, value):
        if "\x00" in value:
            raise ValueError("Null characters are not allowed")
        value.encode("utf-8")
        return value


class OcrDocumentOut(BaseModel):
    id: UUID
    note_id: UUID
    page_id: UUID
    file_id: UUID
    run_id: UUID
    revision: int
    status: Literal["queued", "running", "succeeded", "cancelled", "interrupted", "failed", "truncated"]
    error_code: str | None
    raw_text: str | None
    corrected_text: str | None
    reviewed: bool
    source_valid: bool
    source_error: str | None
    source_revision: int
    page_number: int
    region: ImageRegion | None
    model: str
    usage: dict[str, int] | None
    created_at: datetime
    updated_at: datetime
