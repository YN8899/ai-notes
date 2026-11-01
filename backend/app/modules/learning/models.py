"""Owner-scoped editable cards and immutable single-attempt generation receipts."""
import uuid
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.shared.db import Base, utcnow
from app.shared.models import Entity


class Deck(Entity, Base):
    __tablename__ = "learning_decks"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("owner_id", "id", name="uq_learning_deck_owner"),
                      CheckConstraint("revision > 0 AND status IN ('active','archived')", name="learning_deck_state"),
                      Index("ix_learning_decks_owner", "owner_id", "updated_at"))


class CardGeneration(Entity, Base):
    __tablename__ = "card_generations"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    deck_id: Mapped[uuid.UUID] = mapped_column()
    run_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    request_key: Mapped[uuid.UUID] = mapped_column()
    request_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))
    count: Mapped[int] = mapped_column(Integer)
    source: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error_code: Mapped[str | None] = mapped_column(String(64))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_card_generation_owner"),
        UniqueConstraint("owner_id", "request_key", name="uq_card_generation_request"),
        ForeignKeyConstraint(["owner_id", "deck_id"], ["learning_decks.owner_id", "learning_decks.id"], name="fk_generation_deck_owner"),
        ForeignKeyConstraint(["owner_id", "run_id"], ["ai_runs.owner_id", "ai_runs.id"], name="fk_generation_run_owner"),
        CheckConstraint("status IN ('pending','succeeded','failed') AND count BETWEEN 1 AND 20", name="card_generation_state"),
        CheckConstraint("kind IN ('qa','cloze','occlusion','formula')", name="card_generation_kind"),
        Index("ix_card_generations_owner", "owner_id", "created_at"))


class Card(Entity, Base):
    __tablename__ = "learning_cards"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    deck_id: Mapped[uuid.UUID] = mapped_column()
    kind: Mapped[str] = mapped_column(String(16))
    front: Mapped[str] = mapped_column(Text)
    back: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    source: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    generation_id: Mapped[uuid.UUID | None] = mapped_column()
    generation_ordinal: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_learning_card_owner"),
        UniqueConstraint("generation_id", "generation_ordinal", name="uq_card_generation_ordinal"),
        ForeignKeyConstraint(["owner_id", "deck_id"], ["learning_decks.owner_id", "learning_decks.id"], name="fk_card_deck_owner"),
        ForeignKeyConstraint(["owner_id", "generation_id"], ["card_generations.owner_id", "card_generations.id"], name="fk_card_generation_owner"),
        CheckConstraint("revision > 0 AND status IN ('draft','confirmed','archived')", name="learning_card_state"),
        CheckConstraint("kind IN ('qa','cloze','occlusion','formula')", name="learning_card_kind"),
        CheckConstraint("length(front) BETWEEN 1 AND 8000 AND length(back) <= 8000", name="learning_card_size"),
        CheckConstraint("(generation_id IS NULL AND generation_ordinal IS NULL) OR (generation_id IS NOT NULL AND generation_ordinal BETWEEN 0 AND 19)", name="learning_card_generation_pair"),
        Index("ix_learning_cards_owner", "owner_id", "deck_id", "status", "updated_at"))
