"""User timezone preferences and durable local-date reminder deduplication."""
import uuid
from datetime import date, datetime
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.shared.db import Base, utcnow
from app.shared.models import Entity


class LearningSettings(Entity, Base):
    __tablename__ = "user_learning_settings"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    review_time: Mapped[str] = mapped_column(String(5), default="20:00")
    reminder_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("revision > 0", name="learning_settings_revision"),
        CheckConstraint("review_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'", name="learning_settings_time"),
        Index("ix_learning_settings_check", "last_checked_at", "owner_id"),
    )


class DailyDigest(Entity, Base):
    __tablename__ = "daily_review_digests"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(32), default="daily_review")
    local_date: Mapped[date] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(64))
    counts: Mapped[dict] = mapped_column(JSONB)
    # Account archives preserve history without restoring runnable Job/outbox
    # identities. Whole-site backups retain the original link.
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"), unique=True)
    __table_args__ = (
        UniqueConstraint("owner_id", "kind", "local_date", name="uq_daily_review_digest"),
        CheckConstraint("kind = 'daily_review' AND jsonb_typeof(counts) = 'object'", name="daily_review_digest_shape"),
        Index("ix_daily_review_digest_owner", "owner_id", "local_date"),
    )
