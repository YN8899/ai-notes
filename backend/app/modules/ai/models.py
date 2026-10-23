import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db import Base, utcnow
from app.shared.models import Entity


class AiCredential(Entity, Base):
    # Preserve the schema for already-applied migrations; shared credentials are not stored here.
    __tablename__ = "ai_credentials"

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    provider: Mapped[str] = mapped_column(String(32))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    key_suffix: Mapped[str] = mapped_column(String(4))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_test_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    test_status: Mapped[str | None] = mapped_column(String(16))
    test_code: Mapped[str | None] = mapped_column(String(32))
    available_models: Mapped[list[str]] = mapped_column(JSONB, default=list)

    __table_args__ = (
        # This index also covers owner-scoped listing and the owner foreign key.
        UniqueConstraint("owner_id", "provider", name="uq_ai_credentials_owner_provider"),
        CheckConstraint("revision > 0 AND key_version > 0", name="ai_credential_versions"),
        CheckConstraint("octet_length(ciphertext) > 28", name="ai_credential_ciphertext"),
        CheckConstraint("test_status IS NULL OR test_status IN ('valid','invalid','unavailable')", name="ai_credential_test_status"),
    )
