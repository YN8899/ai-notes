"""Strict card editing contracts. All rectangles use full-page normalized coordinates."""
import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.modules.ai.image_schemas import ImageInput, ImageRegion, ImageSourceOut
from app.modules.ai.text_schemas import TextInput, TextSourceOut
from app.modules.ai.schemas import PdfInput, PdfSourceOut
from app.modules.notes.schemas import validate_pg_text

Kind = Literal["qa", "cloze", "occlusion", "formula"]
Status = Literal["draft", "confirmed", "archived"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextSelection(TextInput):
    kind: Literal["text"]


class ImageSelection(ImageInput):
    kind: Literal["image"]


class PdfSelection(PdfInput):
    kind: Literal["pdf"]

    @model_validator(mode="after")
    def single_page(self):
        if self.mode == "all" or len(self.page_numbers) != 1:
            raise ValueError("Cards require exactly one PDF page")
        return self


Selection = Annotated[TextSelection | ImageSelection | PdfSelection, Field(discriminator="kind")]
PublicSource = TextSourceOut | ImageSourceOut | PdfSourceOut


class Masks(Input):
    masks: list[ImageRegion] = Field(min_length=1, max_length=20)


class CardContent(Input):
    kind: Kind
    front: str = Field(min_length=1, max_length=8000)
    back: str = Field(default="", max_length=8000)
    payload: Masks | None = None

    @field_validator("front", "back")
    @classmethod
    def valid_text(cls, value):
        return validate_pg_text(value)

    @model_validator(mode="after")
    def valid_content(self):
        if not self.front.strip() or self.kind == "qa" and not self.back.strip():
            raise ValueError("Card text is empty")
        if self.kind == "cloze":
            marker = r"(?<!\{)\{\{([^{}]*)\}\}(?!\})"
            matches = re.findall(marker, self.front)
            residue = re.sub(marker, "", self.front)
            if not matches or any(not item.strip() for item in matches) or "{{" in residue or "}}" in residue:
                raise ValueError("Cloze requires balanced nonempty answer markers")
        if (self.kind == "occlusion") != (self.payload is not None):
            raise ValueError("Only occlusion cards require masks")
        return self


class CardCreate(CardContent):
    deck_id: UUID
    status: Status = "draft"
    source: Selection | None = None


class CardPatch(Input):
    revision: int = Field(strict=True, ge=1)
    deck_id: UUID | None = None
    kind: Kind | None = None
    front: str | None = Field(default=None, min_length=1, max_length=8000)
    back: str | None = Field(default=None, max_length=8000)
    payload: Masks | None = None
    status: Status | None = None
    source: Selection | None = None

    @model_validator(mode="after")
    def no_null_content(self):
        if any(getattr(self, key) is None for key in self.model_fields_set & {"deck_id", "kind", "front", "back", "status"}):
            raise ValueError("Content fields cannot be null")
        return self


class DeckCreate(Input):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        validate_pg_text(value)
        if not value.strip():
            raise ValueError("Deck name cannot be empty")
        return value.strip()


class DeckPatch(Input):
    revision: int = Field(strict=True, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: Literal["active", "archived"] | None = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        return DeckCreate(name=value).name if value is not None else value


class DeckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    status: Literal["active", "archived"]
    revision: int
    created_at: datetime
    updated_at: datetime


class CardOut(BaseModel):
    id: UUID
    deck_id: UUID
    kind: Kind
    front: str
    back: str
    payload: Masks | None
    status: Status
    revision: int
    source: PublicSource | None
    source_valid: bool
    source_error: str | None
    generation_id: UUID | None
    created_at: datetime
    updated_at: datetime


class ReviewItem(Input):
    id: UUID
    revision: int = Field(strict=True, ge=1)


class BatchReview(Input):
    action: Literal["confirm", "archive"]
    cards: list[ReviewItem] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({item.id for item in self.cards}) != len(self.cards):
            raise ValueError("Duplicate cards")
        return self


class DeckList(BaseModel):
    items: list[DeckOut]
    total: int
    offset: int
    limit: int


class CardList(BaseModel):
    items: list[CardOut]
    total: int
    offset: int
    limit: int


class SourceRead(BaseModel):
    source: PublicSource
    source_changed: bool
    text: str | None = None
    file_id: UUID | None = None
    page_id: UUID | None = None
    page_number: int | None = None


class GenerationPreview(Input):
    deck_id: UUID
    kind: Kind
    count: int = Field(strict=True, ge=1, le=20)
    source: Selection
    provider: str = Field(default="deepseek", pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    model: str = Field(default="deepseek-flash", min_length=1, max_length=256)


class GenerationCreate(GenerationPreview):
    request_key: UUID
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_external: Literal[True]

    @field_validator("confirm_external", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit external-call confirmation required")
        return value


class GenerationPreviewOut(BaseModel):
    preview_hash: str
    source: PublicSource
    kind: Kind
    count: int
    model: str
    provider: str = "deepseek"
    planned_calls: Literal[1] = 1


class GenerationOut(BaseModel):
    id: UUID
    deck_id: UUID
    request_key: UUID
    kind: Kind
    count: int
    model: str
    provider: str = "deepseek"
    status: Literal["pending", "succeeded", "failed"]
    run_status: str
    usage: dict | None
    error_code: str | None
    source: PublicSource
    card_ids: list[UUID]
    created_at: datetime
    finished_at: datetime | None


class GenerationList(BaseModel):
    items: list[GenerationOut]
    total: int
    offset: int
    limit: int
