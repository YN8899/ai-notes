import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Text, DateTime, ForeignKey, ForeignKeyConstraint, UniqueConstraint, CheckConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.shared.db import Base, utcnow
from app.shared.models import Entity


class OwnedRevision:
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Folder(Entity, OwnedRevision, Base):
    __tablename__ = "folders"
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[uuid.UUID | None] = mapped_column()
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_folders_owner_id"),
        ForeignKeyConstraint(["owner_id", "parent_id"], ["folders.owner_id", "folders.id"], name="fk_folder_parent_owner"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="folder_not_self"),
        CheckConstraint("revision > 0", name="folder_revision"),
        Index("ix_folders_owner_parent", "owner_id", "parent_id"),
    )


class Notebook(Entity, OwnedRevision, Base):
    __tablename__ = "notebooks"
    name: Mapped[str] = mapped_column(String(120))
    folder_id: Mapped[uuid.UUID | None] = mapped_column()
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_notebooks_owner_id"),
        ForeignKeyConstraint(["owner_id", "folder_id"], ["folders.owner_id", "folders.id"], name="fk_notebook_folder_owner"),
        CheckConstraint("revision > 0", name="notebook_revision"),
        Index("ix_notebooks_owner_folder", "owner_id", "folder_id"),
    )


class Tag(Entity, OwnedRevision, Base):
    __tablename__ = "tags"
    name: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_tags_owner_id"),
        UniqueConstraint("owner_id", "name", name="uq_tags_owner_name"),
        CheckConstraint("revision > 0", name="tag_revision"),
    )


class Note(Entity, OwnedRevision, Base):
    __tablename__ = "notes"
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16), default="text")
    notebook_id: Mapped[uuid.UUID | None] = mapped_column()
    body_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    plain_text: Mapped[str] = mapped_column(Text, default="")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_notes_owner_id"),
        ForeignKeyConstraint(["owner_id", "notebook_id"], ["notebooks.owner_id", "notebooks.id"], name="fk_note_notebook_owner"),
        CheckConstraint("kind IN ('text','image')", name="note_kind"),
        CheckConstraint("revision > 0", name="note_revision"),
        CheckConstraint("kind <> 'image' OR (body_json = '{}'::jsonb AND plain_text = '')", name="image_note_no_body"),
        Index("ix_notes_owner_notebook", "owner_id", "notebook_id"),
        Index("ix_notes_owner_deleted_updated", "owner_id", "deleted_at", "updated_at"),
    )


class NoteTag(Base):
    __tablename__ = "note_tags"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    note_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tag_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    __table_args__ = (
        ForeignKeyConstraint(["owner_id", "note_id"], ["notes.owner_id", "notes.id"], name="fk_note_tag_note_owner"),
        ForeignKeyConstraint(["owner_id", "tag_id"], ["tags.owner_id", "tags.id"], name="fk_note_tag_tag_owner"),
        Index("ix_note_tags_owner_tag", "owner_id", "tag_id"),
    )


class NoteVersion(Entity, Base):
    __tablename__ = "note_versions"
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    note_id: Mapped[uuid.UUID] = mapped_column()
    revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        ForeignKeyConstraint(["owner_id", "note_id"], ["notes.owner_id", "notes.id"], name="fk_note_version_owner"),
        UniqueConstraint("note_id", "revision", name="uq_note_versions_revision"),
        CheckConstraint("revision > 0", name="note_version_revision"),
    )
