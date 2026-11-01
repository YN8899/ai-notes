"""Explicit paid-operation contracts; configuration and secrets remain server owned."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from app.modules.search.keyword_schemas import Keyword, SearchHitOut


class SemanticConfigOut(BaseModel):
    provider: str
    workspace: str
    model: str
    dimensions: int
    chunker: str
    fingerprint: str


class SemanticStatusOut(BaseModel):
    enabled: bool
    configured: bool
    available: bool
    reason: str | None
    config: SemanticConfigOut | None
    active_index_id: UUID | None
    building_index_id: UUID | None


class SemanticPreviewOut(BaseModel):
    manifest_hash: str
    config: SemanticConfigOut
    source_count: int
    chunk_count: int
    character_count: int
    batch_count: int
    max_chunks: int
    sources: dict[str, int]


class SemanticIndexCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_external: Literal[True]


class SemanticIndexOut(BaseModel):
    id: UUID
    request_key: UUID
    status: Literal["queued", "building", "ready", "active", "failed"]
    config: SemanticConfigOut
    manifest_hash: str
    source_count: int
    chunk_count: int
    completed_chunks: int
    usage: dict[str, int]
    error_code: str | None
    job_id: UUID
    created_at: datetime
    finished_at: datetime | None


class SemanticQueryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: UUID
    q: Keyword
    confirm_external: Literal[True]
    limit: int = Field(default=20, strict=True, ge=1, le=20)
    kind: Literal["text", "image"] | None = None
    notebook_id: UUID | None = None
    folder_id: UUID | None = None
    tag_id: UUID | None = None


class SemanticHitOut(SearchHitOut):
    chunk_id: UUID
    score: float


class SemanticQueryOut(BaseModel):
    id: UUID
    request_key: UUID
    index_id: UUID
    status: Literal["running", "succeeded", "failed"]
    error_code: str | None
    usage: dict[str, int]
    items: list[SemanticHitOut]
    created_at: datetime
    finished_at: datetime | None
