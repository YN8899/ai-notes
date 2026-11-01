"""Independent owner-scoped plans and append-only review facts."""
import uuid
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.shared.db import Base, utcnow
from app.shared.models import Entity


class ReviewItem(Entity, Base):
    __tablename__ = "review_items"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(16))
    note_id: Mapped[uuid.UUID | None] = mapped_column()
    card_id: Mapped[uuid.UUID | None] = mapped_column()
    quiz_id: Mapped[uuid.UUID | None] = mapped_column()
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_review_item_owner"),
        *(UniqueConstraint("owner_id", key, name="uq_review_item_" + key) for key in ("note_id", "card_id", "quiz_id")),
        ForeignKeyConstraint(["owner_id", "note_id"], ["notes.owner_id", "notes.id"], name="fk_review_item_note_owner"),
        ForeignKeyConstraint(["owner_id", "card_id"], ["learning_cards.owner_id", "learning_cards.id"], name="fk_review_item_card_owner"),
        ForeignKeyConstraint(["owner_id", "quiz_id"], ["learning_quizzes.owner_id", "learning_quizzes.id"], name="fk_review_item_quiz_owner"),
        CheckConstraint("(kind = 'note' AND note_id IS NOT NULL AND card_id IS NULL AND quiz_id IS NULL) OR (kind = 'card' AND card_id IS NOT NULL AND note_id IS NULL AND quiz_id IS NULL) OR (kind = 'quiz' AND quiz_id IS NOT NULL AND note_id IS NULL AND card_id IS NULL)", name="review_item_exact_source"),
    )


class ReviewPlan(Entity, Base):
    __tablename__ = "review_plans"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    item_id: Mapped[uuid.UUID] = mapped_column()
    strategy: Mapped[str] = mapped_column(String(16), default="manual")
    status: Mapped[str] = mapped_column(String(16), default="active")
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    algorithm_version: Mapped[str] = mapped_column(String(40))
    scheduler_state: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_fsrs_state: Mapped[dict | None] = mapped_column(JSONB)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_review_plan_owner"),
        UniqueConstraint("owner_id", "id", "item_id", name="uq_review_plan_item_owner"),
        ForeignKeyConstraint(["owner_id", "item_id"], ["review_items.owner_id", "review_items.id"], name="fk_review_plan_item_owner"),
        CheckConstraint("revision > 0 AND strategy IN ('manual','fsrs') AND status IN ('active','paused','archived')", name="review_plan_state"),
        CheckConstraint("jsonb_typeof(scheduler_state) = 'object' AND (last_fsrs_state IS NULL OR jsonb_typeof(last_fsrs_state) = 'object')", name="review_plan_json"),
        Index("uq_review_plan_effective", "owner_id", "item_id", unique=True, postgresql_where=text("status = 'active' OR status = 'paused'")),
        Index("ix_review_plans_due", "owner_id", "status", "next_due_at"),
    )


class ReviewLog(Entity, Base):
    __tablename__ = "review_logs"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    item_id: Mapped[uuid.UUID] = mapped_column()
    plan_id: Mapped[uuid.UUID | None] = mapped_column()
    request_key: Mapped[uuid.UUID] = mapped_column()
    request_hash: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16))
    rating: Mapped[int | None] = mapped_column(Integer)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    old_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    new_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    result_plan: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint("owner_id", "request_key", name="uq_review_log_request"),
        ForeignKeyConstraint(["owner_id", "item_id"], ["review_items.owner_id", "review_items.id"], name="fk_review_log_item_owner"),
        ForeignKeyConstraint(["owner_id", "plan_id", "item_id"], ["review_plans.owner_id", "review_plans.id", "review_plans.item_id"], name="fk_review_log_plan_item_owner"),
        CheckConstraint("action IN ('create','review','practice','reschedule','switch','pause','resume','archive') AND (rating IS NULL OR rating BETWEEN 1 AND 4)", name="review_log_action"),
        CheckConstraint("(action = 'practice' OR plan_id IS NOT NULL) AND (action NOT IN ('review','practice') OR rating IS NOT NULL)", name="review_log_plan_rating"),
        Index("ix_review_logs_history", "owner_id", "item_id", "reviewed_at"),
    )
