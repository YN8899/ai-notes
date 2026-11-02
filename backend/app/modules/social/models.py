import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db import Base, utcnow
from app.shared.models import Entity


class SocialProfile(Entity, Base):
    __tablename__ = "social_profiles"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    bio: Mapped[str] = mapped_column(String(240), default="")
    subject: Mapped[str] = mapped_column(String(80), default="")
    details: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(16), default="friends")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        CheckConstraint("visibility IN ('friends','private')", name="social_profile_visibility"),
        CheckConstraint("revision > 0", name="social_profile_revision"),
    )


class Friendship(Entity, Base):
    __tablename__ = "friendships"
    user_low_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    user_high_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    requester_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (
        UniqueConstraint("user_low_id", "user_high_id", name="uq_friendship_pair"),
        CheckConstraint("user_low_id < user_high_id", name="friendship_canonical_pair"),
        CheckConstraint("requester_id IN (user_low_id,user_high_id)", name="friendship_requester"),
        CheckConstraint("status IN ('pending','accepted','rejected','removed')", name="friendship_status"),
        CheckConstraint("revision > 0", name="friendship_revision"),
        Index("ix_friendships_low_status", "user_low_id", "status"),
        Index("ix_friendships_high_status", "user_high_id", "status"),
    )


class Follow(Entity, Base):
    __tablename__ = "follows"
    follower_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    followee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    __table_args__ = (
        UniqueConstraint("follower_id", "followee_id", name="uq_follow_pair"),
        CheckConstraint("follower_id <> followee_id", name="follow_not_self"),
    )


class NoteGrant(Entity, Base):
    __tablename__ = "note_grants"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    note_id: Mapped[uuid.UUID] = mapped_column()
    grantee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(["owner_id", "note_id"], ["notes.owner_id", "notes.id"], name="fk_note_grant_owner"),
        UniqueConstraint("note_id", "grantee_id", name="uq_note_grant_recipient"),
        CheckConstraint("owner_id <> grantee_id", name="note_grant_not_self"),
        CheckConstraint("revision > 0", name="note_grant_revision"),
    )
