from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.modules.notes.schemas import Input, Revision, Title, validate_body, validate_pg_text

PostType = Literal["note", "question", "experience", "resource"]


class CommunityPostCreate(Input):
    title: Title
    body_json: dict = Field(default_factory=dict)
    plain_text: str = Field(default="", max_length=1_000_000)
    subject: str = Field(default="", max_length=80)
    post_type: PostType = "note"
    tags: list[str] = Field(default_factory=list, max_length=12)
    source_note_id: UUID | None = None
    source_revision: Revision | None = None

    @model_validator(mode="after")
    def validate_content(self):
        validate_body(self.body_json, self.plain_text)
        if (self.source_note_id is None) != (self.source_revision is None):
            raise ValueError("Source note and revision must be provided together")
        normalized = []
        for tag in self.tags:
            tag = validate_pg_text(tag).strip()
            if not 1 <= len(tag) <= 32:
                raise ValueError("Tags must be between 1 and 32 characters")
            if tag in normalized:
                raise ValueError("Duplicate tags")
            normalized.append(tag)
        self.tags = normalized
        return self


class CommunityPostPatch(CommunityPostCreate):
    revision: Revision


class CommunityPublishIn(Input):
    revision: Revision
    request_key: UUID


class CommunityPublishOut(BaseModel):
    post_id: UUID
    snapshot_id: UUID
    job_id: UUID | None
    status: Literal["preparing", "ready", "failed", "cancelled"]


class CommunityAuthorOut(BaseModel):
    id: UUID
    username: str
    display_name: str


class CommunityAssetOut(BaseModel):
    id: UUID
    position: int
    filename: str
    width: int | None
    height: int | None
    image_url: str
    original_url: str


class CommunityPostSummary(BaseModel):
    id: UUID
    author: CommunityAuthorOut
    title: str
    subject: str
    post_type: PostType
    tags: list[str]
    excerpt: str
    published_at: datetime
    snapshot_version: int
    like_count: int
    comment_count: int
    liked: bool
    bookmarked: bool
    recommendation_reason: str


class CommunityPostOut(CommunityPostSummary):
    body_json: dict
    plain_text: str
    assets: list[CommunityAssetOut]
    source_revision: int | None


class CommunityPostList(BaseModel):
    items: list[CommunityPostSummary]
    next_cursor: str | None
    limit: int


class CommunityDraftOut(BaseModel):
    id: UUID
    title: str
    body_json: dict
    plain_text: str
    subject: str
    post_type: PostType
    tags: list[str]
    source_note_id: UUID | None
    source_revision: int | None
    revision: int
    status: Literal["draft", "published", "withdrawn", "hidden"]
    live_snapshot_id: UUID | None
    preparing_snapshot_id: UUID | None
    publish_status: Literal["preparing", "ready", "failed", "cancelled"] | None
    publish_error: str | None
    moderation_reason: str | None
    created_at: datetime
    updated_at: datetime


class CommunityDraftList(BaseModel):
    items: list[CommunityDraftOut]
    next_cursor: str | None
    limit: int


class CommunityCommentCreate(Input):
    body: str = Field(min_length=1, max_length=4000)
    request_key: UUID

    @model_validator(mode="after")
    def not_blank(self):
        if not self.body.strip():
            raise ValueError("Comment cannot be blank")
        return self


class CommunityCommentOut(BaseModel):
    id: UUID
    post_id: UUID
    author: CommunityAuthorOut
    body: str
    created_at: datetime


class CommunityCommentList(BaseModel):
    items: list[CommunityCommentOut]
    next_cursor: str | None
    limit: int


class CommunityReportCreate(Input):
    reason: str = Field(min_length=1, max_length=500)
    request_key: UUID

    @model_validator(mode="after")
    def not_blank(self):
        if not self.reason.strip():
            raise ValueError("Reason cannot be blank")
        return self


class CommunityReportOut(BaseModel):
    id: UUID
    post_id: UUID
    reason: str
    status: Literal["pending", "dismissed", "hidden"]
    resolution: str | None
    created_at: datetime
    resolved_at: datetime | None


class CommunityReportList(BaseModel):
    items: list[CommunityReportOut]
    next_cursor: str | None
    limit: int


class CommunityModerationIn(Input):
    decision: Literal["hide", "restore"]
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def not_blank(self):
        if not self.reason.strip():
            raise ValueError("Reason cannot be blank")
        return self


class CommunityReportResolve(Input):
    decision: Literal["dismiss", "hide"]
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def not_blank(self):
        if not self.reason.strip():
            raise ValueError("Reason cannot be blank")
        return self
