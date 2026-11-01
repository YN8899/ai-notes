import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType
from app.shared.db import Base
from app.shared.models import Entity


class Vector(UserDefinedType):
    """pgvector wire text avoids an additional Python dependency."""
    cache_ok = True

    def get_col_spec(self, **kw):
        return "vector"

    def bind_processor(self, dialect):
        return lambda value: None if value is None else "[" + ",".join(str(float(x)) for x in value) + "]"

    def result_processor(self, dialect, coltype):
        return lambda value: None if value is None else [float(x) for x in value.strip("[]").split(",")]


ischema_names["vector"] = Vector


class SemanticIndex(Entity, Base):
    __tablename__ = "semantic_indexes"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    request_key: Mapped[uuid.UUID] = mapped_column()
    status: Mapped[str] = mapped_column(String(16))
    config: Mapped[dict] = mapped_column(JSONB)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    source_count: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    completed_chunks: Mapped[int] = mapped_column(Integer, default=0)
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), unique=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("owner_id", "request_key", name="uq_semantic_index_request"),
        CheckConstraint("status IN ('queued','building','ready','active','failed')", name="semantic_index_status"),
        CheckConstraint("chunk_count BETWEEN 1 AND 500 AND completed_chunks BETWEEN 0 AND chunk_count", name="semantic_index_counts"),
        Index("uq_semantic_active", "owner_id", unique=True, postgresql_where=text("status = 'active'")),
        Index("uq_semantic_building", "owner_id", unique=True, postgresql_where=text("status = 'queued' OR status = 'building'")),
    )


class SemanticChunk(Entity, Base):
    __tablename__ = "semantic_chunks"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    index_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("semantic_indexes.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    source_key: Mapped[str] = mapped_column(String(160))
    source_hash: Mapped[str] = mapped_column(String(64))
    source: Mapped[dict] = mapped_column(JSONB)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    dimensions: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list | None] = mapped_column(Vector())
    __table_args__ = (
        UniqueConstraint("index_id", "ordinal", name="uq_semantic_chunk_ordinal"),
        CheckConstraint('"start" >= 0 AND "end" > "start" AND "end" - "start" = length(text) AND length(text) <= 800', name="semantic_chunk_range"),
        CheckConstraint("embedding IS NULL OR vector_dims(embedding) = dimensions", name="semantic_chunk_dimensions"),
        CheckConstraint("dimensions IN (64,128,256,512,768,1024,1536,2048)", name="semantic_chunk_dimension_supported"),
    )


class SemanticQuery(Entity, Base):
    __tablename__ = "semantic_queries"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    request_key: Mapped[uuid.UUID] = mapped_column()
    request_hash: Mapped[str] = mapped_column(String(64))
    index_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("semantic_indexes.id"))
    status: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSONB)
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    hits: Mapped[list] = mapped_column(JSONB, default=list)
    error_code: Mapped[str | None] = mapped_column(String(64))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("owner_id", "request_key", name="uq_semantic_query_request"),
        CheckConstraint("status IN ('running','succeeded','failed')", name="semantic_query_status"),
    )


class SemanticCall(Entity, Base):
    __tablename__ = "semantic_calls"
    index_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("semantic_indexes.id"))
    batch: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (UniqueConstraint("index_id", "batch", name="uq_semantic_call_batch"),
                     CheckConstraint("status IN ('started','succeeded','failed')", name="semantic_call_status"))
