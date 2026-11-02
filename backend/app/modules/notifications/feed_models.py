"""Transactional owner cursor; deliberately no PostgreSQL sequence object."""
import uuid
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.shared.db import Base


class NotificationCounter(Base):
    __tablename__ = "notification_counters"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    head_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    read_revision: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (CheckConstraint("head_seq >= 0 AND read_revision >= 0", name="notification_counter_bounds"),)
