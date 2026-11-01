"""Short owner-scoped activity facts, separate from learning outcomes."""
import uuid
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.shared.db import Base
from app.shared.models import Entity


class LearningEvent(Entity, Base):
    __tablename__ = "learning_events"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    event_id: Mapped[uuid.UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(String(16))
    note_id: Mapped[uuid.UUID | None] = mapped_column()
    card_id: Mapped[uuid.UUID | None] = mapped_column()
    quiz_id: Mapped[uuid.UUID | None] = mapped_column()
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    request_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("owner_id", "event_id", name="uq_learning_event_request"),
        ForeignKeyConstraint(["owner_id", "note_id"], ["notes.owner_id", "notes.id"], name="fk_learning_event_note_owner"),
        ForeignKeyConstraint(["owner_id", "card_id"], ["learning_cards.owner_id", "learning_cards.id"], name="fk_learning_event_card_owner"),
        ForeignKeyConstraint(["owner_id", "quiz_id"], ["learning_quizzes.owner_id", "learning_quizzes.id"], name="fk_learning_event_quiz_owner"),
        CheckConstraint("num_nonnulls(note_id,card_id,quiz_id) = 1 AND ((kind = 'reading' AND note_id IS NOT NULL) OR (kind = 'card_practice' AND card_id IS NOT NULL) OR (kind = 'quiz' AND quiz_id IS NOT NULL) OR kind = 'review')", name="learning_event_source_kind"),
        CheckConstraint("ended_at > started_at AND ended_at <= started_at + interval '60 seconds'", name="learning_event_duration"),
        Index("ix_learning_events_owner_time", "owner_id", "started_at", "ended_at"),
    )
