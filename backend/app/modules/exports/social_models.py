"""Private, inert recovery history. No foreign account grants are recreated."""
import uuid
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.shared.db import Base
from app.shared.models import Entity

KINDS = ("profile", "friendship", "follow", "note_grant", "chat_thread", "chat_message", "chat_attachment",
         "community_post", "community_snapshot", "community_comment", "community_reaction", "community_report")


class SocialArchiveRecord(Entity, Base):
    __tablename__ = "social_archive_records"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[uuid.UUID] = mapped_column()
    payload: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_social_archive_owner_id"),
        CheckConstraint("kind IN ('profile','friendship','follow','note_grant','chat_thread','chat_message','chat_attachment','community_post','community_snapshot','community_comment','community_reaction','community_report')", name="social_archive_kind"),
        Index("ix_social_archive_owner_source", "owner_id", "kind", "source_id"),
    )


class SocialArchiveAsset(Entity, Base):
    __tablename__ = "social_archive_assets"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    record_id: Mapped[uuid.UUID] = mapped_column(index=True)
    source_id: Mapped[uuid.UUID] = mapped_column()
    role: Mapped[str] = mapped_column(String(16))
    object_key: Mapped[str] = mapped_column(String(256), unique=True)
    filename: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(64))
    bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        ForeignKeyConstraint(["owner_id", "record_id"], ["social_archive_records.owner_id", "social_archive_records.id"], name="fk_social_archive_asset_owner"),
        UniqueConstraint("owner_id", "record_id", "source_id", "role", name="uq_social_archive_asset_source"),
        CheckConstraint("role IN ('original','reading','attachment')", name="social_archive_asset_role"),
        CheckConstraint("bytes > 0 AND bytes <= 52428800", name="social_archive_asset_size"),
    )
