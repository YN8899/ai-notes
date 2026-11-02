import uuid
from sqlalchemy import Boolean, ForeignKey, ForeignKeyConstraint, String, Text, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.shared.db import Base
from app.shared.models import Entity


class PushSubscription(Entity, Base):
    __tablename__ = "push_subscriptions"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(index=True)
    # Never serialize these capability credentials or include them in account ZIPs.
    endpoint_hash: Mapped[str] = mapped_column(String(64), unique=True)
    endpoint: Mapped[str] = mapped_column(Text)
    p256dh: Mapped[str] = mapped_column(String(87))
    auth: Mapped[str] = mapped_column(String(22))
    vapid_key_hash: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        ForeignKeyConstraint(["owner_id", "session_id"], ["sessions.user_id", "sessions.id"], ondelete="CASCADE", name="fk_push_session_owner"),
        Index("ix_push_subscriptions_owner_session", "owner_id", "session_id"),
    )
