import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db import Base, utcnow
from app.shared.models import Entity


class CommunityPost(Entity, Base):
    __tablename__ = "community_posts"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    plain_text: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(String(80), default="")
    post_type: Mapped[str] = mapped_column(String(16), default="note")
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    source_note_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("notes.id", ondelete="SET NULL"))
    source_revision: Mapped[int | None] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    live_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("community_snapshots.id", use_alter=True, name="fk_community_live_snapshot"))
    preparing_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("community_snapshots.id", use_alter=True, name="fk_community_preparing_snapshot"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    moderation_reason: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_community_post_owner"),
        CheckConstraint("revision > 0", name="community_post_revision"),
        CheckConstraint("status IN ('draft','published','withdrawn','hidden')", name="community_post_status"),
        CheckConstraint("post_type IN ('note','question','experience','resource')", name="community_post_type"),
        CheckConstraint("source_revision IS NULL OR source_revision > 0", name="community_post_source_revision"),
        Index("ix_community_posts_feed", "status", "published_at", "id"),
    )


class CommunitySnapshot(Entity, Base):
    __tablename__ = "community_snapshots"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    post_id: Mapped[uuid.UUID] = mapped_column()
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="preparing")
    title: Mapped[str] = mapped_column(String(200))
    body_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    plain_text: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(String(80), default="")
    post_type: Mapped[str] = mapped_column(String(16))
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    # Historical lineage only; it never grants access to the private source.
    source_note_id: Mapped[uuid.UUID | None] = mapped_column()
    source_revision: Mapped[int | None] = mapped_column(Integer)
    request_key: Mapped[uuid.UUID] = mapped_column()
    request_hash: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"), unique=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    recommendation_score: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        ForeignKeyConstraint(["owner_id", "post_id"], ["community_posts.owner_id", "community_posts.id"], name="fk_community_snapshot_owner"),
        UniqueConstraint("owner_id", "id", name="uq_community_snapshot_owner"),
        UniqueConstraint("post_id", "version", name="uq_community_snapshot_version"),
        UniqueConstraint("owner_id", "request_key", name="uq_community_publish_request"),
        CheckConstraint("version > 0", name="community_snapshot_version"),
        CheckConstraint("status IN ('preparing','ready','failed','cancelled')", name="community_snapshot_status"),
        CheckConstraint("recommendation_score BETWEEN 0 AND 3", name="community_snapshot_score"),
        CheckConstraint("post_type IN ('note','question','experience','resource')", name="community_snapshot_type"),
    )


class CommunityAsset(Entity, Base):
    __tablename__ = "community_assets"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column()
    position: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    object_key: Mapped[str] = mapped_column(String(256), unique=True)
    original_object_key: Mapped[str] = mapped_column(String(256), unique=True)
    source_object_key: Mapped[str | None] = mapped_column(String(256))
    source_original_object_key: Mapped[str | None] = mapped_column(String(256))
    bytes: Mapped[int] = mapped_column(BigInteger)
    original_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    original_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    __table_args__ = (
        ForeignKeyConstraint(["owner_id", "snapshot_id"], ["community_snapshots.owner_id", "community_snapshots.id"], name="fk_community_asset_owner"),
        UniqueConstraint("snapshot_id", "position", name="uq_community_asset_position"),
        CheckConstraint("position >= 0 AND bytes > 0 AND original_bytes > 0", name="community_asset_sizes"),
        CheckConstraint("status IN ('pending','ready')", name="community_asset_status"),
    )


class CommunityComment(Entity, Base):
    __tablename__ = "community_comments"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("community_posts.id"), index=True)
    body: Mapped[str] = mapped_column(String(4000))
    request_key: Mapped[uuid.UUID] = mapped_column()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("owner_id", "request_key", name="uq_community_comment_request"),)


class CommunityReaction(Entity, Base):
    __tablename__ = "community_reactions"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("community_posts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    __table_args__ = (
        UniqueConstraint("owner_id", "post_id", "kind", name="uq_community_reaction"),
        CheckConstraint("kind IN ('like','bookmark')", name="community_reaction_kind"),
    )


class CommunityReport(Entity, Base):
    __tablename__ = "community_reports"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("community_posts.id"), index=True)
    reason: Mapped[str] = mapped_column(String(500))
    request_key: Mapped[uuid.UUID] = mapped_column()
    status: Mapped[str] = mapped_column(String(16), default="pending")
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    resolution: Mapped[str | None] = mapped_column(String(500))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("owner_id", "request_key", name="uq_community_report_request"),
        CheckConstraint("status IN ('pending','dismissed','hidden')", name="community_report_status"),
    )
