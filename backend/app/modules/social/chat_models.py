"""Durable private conversations; realtime carries invalidations only."""
import uuid
from datetime import datetime
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.shared.db import Base
from app.shared.models import Entity


class ChatThread(Entity, Base):
    __tablename__ = "chat_threads"
    user_low_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    user_high_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    last_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (UniqueConstraint("user_low_id", "user_high_id", name="chat_thread_pair"), CheckConstraint("user_low_id < user_high_id AND last_seq >= 0", name="chat_thread_order"))


class ChatMessage(Entity, Base):
    __tablename__ = "chat_messages"
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_threads.id"), index=True)
    sender_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    client_message_id: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(BigInteger)
    body: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("sender_id", "client_message_id", name="chat_message_request"), UniqueConstraint("thread_id", "seq", name="chat_message_sequence"), CheckConstraint("seq > 0", name="chat_message_positive_seq"))


class ChatReadCursor(Entity, Base):
    __tablename__ = "chat_read_cursors"
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_threads.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    last_read_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (UniqueConstraint("thread_id", "user_id", name="chat_read_member"), CheckConstraint("last_read_seq >= 0", name="chat_read_positive"))


class ChatAttachment(Entity, Base):
    __tablename__ = "chat_attachments"
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_threads.id"), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chat_messages.id"), index=True)
    object_key: Mapped[str] = mapped_column(String(256), unique=True)
    filename: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(64))
    bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    request_key: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("owner_id", "request_key", name="chat_attachment_request"), CheckConstraint("bytes > 0 AND bytes <= 10485760", name="chat_attachment_size"))


class RealtimeTicket(Entity, Base):
    __tablename__ = "realtime_tickets"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
