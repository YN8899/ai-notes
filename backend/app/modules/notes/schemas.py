from datetime import datetime
import json
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator, field_validator, AfterValidator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
TagName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Revision = Annotated[int, Field(strict=True, ge=1)]
BODY_MAX_BYTES = 1_048_576
BODY_MAX_DEPTH = 64
BODY_MAX_NODES = 50_000


def validate_pg_text(value):
    # PostgreSQL text and JSONB reject NUL. UTF-8 cannot encode isolated
    # surrogates; ordinary newlines, tabs and other valid characters remain valid.
    if "\x00" in value:
        raise ValueError("Text cannot contain NUL")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("Text must be valid UTF-8") from None
    return value


SearchText = Annotated[str, AfterValidator(validate_pg_text)]


def validate_body(body_json, plain_text):
    # Walk iteratively before JSON serialization, so deep editor data does not
    # exhaust the Python stack. {} remains valid for an empty/new note.
    if body_json and (body_json.get("type") != "doc" or not isinstance(body_json.get("content"), list)):
        raise ValueError("Rich text root must be a doc with a content list")
    stack = [(body_json, 0)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if depth > BODY_MAX_DEPTH or nodes > BODY_MAX_NODES:
            raise ValueError("Rich text structure exceeds limits")
        if isinstance(value, dict):
            for key in value:
                validate_pg_text(key)
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
        elif isinstance(value, str):
            validate_pg_text(value)
    validate_pg_text(plain_text)
    encoded = json.dumps(body_json, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) + len(plain_text.encode("utf-8")) > BODY_MAX_BYTES:
        raise ValueError("Note body exceeds 1 MiB")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*")
    @classmethod
    def database_text(cls, value):
        return validate_pg_text(value) if isinstance(value, str) else value


class RevisionIn(Input):
    revision: Revision


class Patch(RevisionIn):
    @model_validator(mode="after")
    def reject_required_null(self):
        nullable = {"parent_id", "folder_id", "notebook_id"}
        for field in self.model_fields_set - nullable:
            if getattr(self, field) is None:
                raise ValueError("Required fields cannot be null")
        if self.model_fields_set == {"revision"}:
            raise ValueError("At least one change is required")
        return self


class FolderCreate(Input):
    name: Name
    parent_id: UUID | None = None


class FolderPatch(Patch):
    name: Name | None = None
    parent_id: UUID | None = None


class NotebookCreate(Input):
    name: Name
    folder_id: UUID | None = None


class NotebookPatch(Patch):
    name: Name | None = None
    folder_id: UUID | None = None


class TagCreate(Input):
    name: TagName


class TagPatch(Patch):
    name: TagName | None = None


class NoteCreate(Input):
    title: Title
    kind: Literal["text", "image"] = "text"
    notebook_id: UUID | None = None
    body_json: dict = Field(default_factory=dict)
    plain_text: str = Field(default="", max_length=1_000_000)
    tag_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_content(self):
        validate_body(self.body_json, self.plain_text)
        if self.kind == "image" and (self.body_json or self.plain_text):
            raise ValueError("Image notes cannot contain rich text")
        if len(set(self.tag_ids)) != len(self.tag_ids):
            raise ValueError("Duplicate tags")
        return self


class NotePatch(Patch):
    title: Title | None = None
    notebook_id: UUID | None = None
    body_json: dict | None = None
    plain_text: str | None = Field(default=None, max_length=1_000_000)
    tag_ids: list[UUID] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def unique_tags(self):
        validate_body(self.body_json or {}, self.plain_text or "")
        if self.tag_ids is not None and len(set(self.tag_ids)) != len(self.tag_ids):
            raise ValueError("Duplicate tags")
        return self


class EntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    owner_id: UUID
    revision: int
    created_at: datetime
    updated_at: datetime


class FolderOut(EntityOut):
    name: str
    parent_id: UUID | None


class NotebookOut(EntityOut):
    name: str
    folder_id: UUID | None


class TagOut(EntityOut):
    name: str


class NoteSummary(EntityOut):
    title: str
    kind: Literal["text", "image"]
    notebook_id: UUID | None
    deleted_at: datetime | None
    tag_ids: list[UUID]


class NoteOut(NoteSummary):
    body_json: dict
    plain_text: str


class NoteList(BaseModel):
    items: list[NoteSummary]
    total: int
    offset: int
    limit: int
    next_cursor: str | None


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    note_id: UUID
    revision: int
    snapshot: dict
    created_at: datetime


class VersionList(BaseModel):
    items: list[VersionOut]
    total: int
    limit: int
    next_cursor: str | None
