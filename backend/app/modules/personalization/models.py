import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db import Base, utcnow
from app.shared.models import Entity


class UserHomePreferences(Base):
    __tablename__ = 'user_home_preferences'
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    preferences: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint('revision > 0', name='home_preferences_revision'),)


class LearningAnalysisPreview(Entity, Base):
    """Expiring consent preparation; operational, never portable account content."""
    __tablename__ = 'learning_analysis_previews'
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id'), index=True)
    auth_session_id: Mapped[uuid.UUID] = mapped_column()
    preferences_revision: Mapped[int] = mapped_column(Integer)
    summary: Mapped[dict] = mapped_column(JSONB)
    input_messages: Mapped[list] = mapped_column(JSONB)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(256))
    provider_snapshot: Mapped[dict] = mapped_column(JSONB)
    preview_hash: Mapped[str] = mapped_column(String(64))
    input_bytes: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (CheckConstraint('input_bytes > 0 AND input_bytes <= 32000 AND preferences_revision > 0', name='analysis_preview_bounds'),)


class LearningAnalysis(Entity, Base):
    __tablename__ = 'learning_analyses'
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id'), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column()
    request_key: Mapped[uuid.UUID] = mapped_column()
    request_hash: Mapped[str] = mapped_column(String(64))
    preview_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        ForeignKeyConstraint(['owner_id', 'run_id'], ['ai_runs.owner_id', 'ai_runs.id'], name='fk_learning_analysis_run_owner'),
        UniqueConstraint('owner_id', 'request_key', name='uq_learning_analysis_request'),
        UniqueConstraint('run_id', name='uq_learning_analysis_run'),
    )
