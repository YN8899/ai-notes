from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator
from app.modules.ai.image_schemas import ImageRegion


def clean_keyword(value):
    if not isinstance(value, str):
        raise ValueError("Keyword must be text")
    value = value.strip()
    if not 1 <= len(value) <= 100 or "\x00" in value:
        raise ValueError("Keyword must contain 1 to 100 characters")
    value.encode("utf-8")
    return value


Keyword = Annotated[str, BeforeValidator(clean_keyword)]
HitKind = Literal["note", "ocr"]
MatchField = Literal["title", "tag", "body", "ocr"]


class SearchHitOut(BaseModel):
    id: UUID
    kind: HitKind
    note_id: UUID
    title: str
    note_kind: Literal["text", "image"]
    matched_field: MatchField
    snippet: str
    match_start: int
    match_end: int
    match_hash: str
    source_revision: int
    ocr_revision: int | None
    page_id: UUID | None
    page_number: int | None
    region: ImageRegion | None
    reviewed: bool | None
    updated_at: datetime


class SearchPageOut(BaseModel):
    items: list[SearchHitOut]
    next_cursor: str | None
    limit: int


class SearchSourceOut(BaseModel):
    id: UUID
    kind: HitKind
    note_id: UUID
    title: str
    matched_field: MatchField
    text: str
    match_start: int
    match_end: int
    match_hash: str
    source_revision: int
    ocr_revision: int | None
    page_id: UUID | None
    page_number: int | None
    file_id: UUID | None
    region: ImageRegion | None
    reviewed: bool | None
