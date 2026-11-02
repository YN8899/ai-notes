import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db import Base, utcnow
from app.shared.models import Entity


class OfflineSyncState(Base):
    """Nonportable instance generation, preserved only by whole-site restore.

    An account ZIP deliberately cannot recreate this state even when it retains
    the user UUID. An old device must never replay operations into that new
    instance simply because it authenticates again as the same owner.
    """
    __tablename__ = "offline_sync_states"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    epoch: Mapped[uuid.UUID] = mapped_column(default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OfflineSyncReceipt(Entity, Base):
    """Permanent success facts; never expire or clear these on a site restore.

    Only small transport identifiers are persisted. Payloads, URLs, upload bytes,
    user content, and authentication capabilities have no place in this table.
    """
    __tablename__ = "offline_sync_receipts"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    operation_id: Mapped[uuid.UUID] = mapped_column()
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[uuid.UUID] = mapped_column()
    resource_mapping: Mapped[dict] = mapped_column(JSONB, default=dict)
    applied_revision: Mapped[int | None] = mapped_column(Integer)
    transport: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        UniqueConstraint("owner_id", "operation_id", name="uq_offline_receipt_operation"),
        CheckConstraint("applied_revision IS NULL OR applied_revision > 0", name="offline_receipt_revision"),
    )
