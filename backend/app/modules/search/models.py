import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db import Base, utcnow
from app.shared.models import Entity


class OcrDocument(Entity, Base):
    __tablename__ = "ocr_documents"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    note_id: Mapped[uuid.UUID] = mapped_column()
    # Historical identity: page removal must not delete the transcript/correction.
    page_id: Mapped[uuid.UUID] = mapped_column()
    file_id: Mapped[uuid.UUID] = mapped_column()
    run_id: Mapped[uuid.UUID] = mapped_column()
    request_key: Mapped[uuid.UUID] = mapped_column()
    request_hash: Mapped[str] = mapped_column(String(64))
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    source_width: Mapped[int | None] = mapped_column(Integer)
    source_height: Mapped[int | None] = mapped_column(Integer)
    corrected_text: Mapped[str | None] = mapped_column(Text)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        ForeignKeyConstraint(["owner_id", "note_id"], ["notes.owner_id", "notes.id"], name="fk_ocr_note_owner"),
        ForeignKeyConstraint(["owner_id", "file_id"], ["files.owner_id", "files.id"], name="fk_ocr_file_owner"),
        ForeignKeyConstraint(["owner_id", "run_id"], ["ai_runs.owner_id", "ai_runs.id"], name="fk_ocr_run_owner"),
        UniqueConstraint("owner_id", "request_key", name="uq_ocr_request"),
        UniqueConstraint("run_id", name="uq_ocr_run"),
        CheckConstraint("revision > 0", name="ocr_revision"),
        CheckConstraint("corrected_text IS NULL OR length(corrected_text) <= 64000", name="ocr_text_length"),
        CheckConstraint("NOT reviewed OR corrected_text IS NOT NULL", name="ocr_reviewed_text"),
        CheckConstraint("(source_width IS NULL OR source_width > 0) AND (source_height IS NULL OR source_height > 0)", name="ocr_source_dimensions"),
        Index("ix_ocr_owner_page", "owner_id", "note_id", "page_id", "created_at"),
    )
