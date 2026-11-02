from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.notes import schemas as ns
from app.modules.files import schemas as fs


class Input(ns.Input):
    model_config = ConfigDict(extra="forbid")


class Empty(Input):
    pass


class FolderUpdate(Input):
    name: ns.Name | None = None
    parent_id: UUID | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or ("name" in self.model_fields_set and self.name is None):
            raise ValueError("At least one valid change is required")
        return self


class NotebookUpdate(Input):
    name: ns.Name | None = None
    folder_id: UUID | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or ("name" in self.model_fields_set and self.name is None):
            raise ValueError("At least one valid change is required")
        return self


class NoteUpdate(Input):
    title: ns.Title | None = None
    notebook_id: UUID | None = None
    body_json: dict | None = None
    plain_text: str | None = Field(default=None, max_length=1_000_000)
    tag_ids: list[UUID] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def nonempty(self):
        # Reuse the canonical note validation, including null/body/tag checks.
        ns.NotePatch(revision=1, **self.model_dump(exclude_unset=True))
        return self


class PageOrder(Input):
    page_ids: list[UUID] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.page_ids)) != len(self.page_ids):
            raise ValueError("Duplicate pages")
        return self


class PageRemove(Input):
    note_id: UUID


class UploadCreate(Input):
    note_id: UUID
    filename: str = Field(min_length=1, max_length=200)
    mime: Literal["image/jpeg", "image/png", "image/webp"]
    size: int = Field(strict=True, gt=0)


class PdfCreate(Input):
    filename: str = Field(min_length=1, max_length=200)
    size: int = Field(strict=True, gt=0)
    notebook_id: UUID | None = None
    tag_ids: list[UUID] = Field(default_factory=list, max_length=100)


class Operation(Input):
    sync_epoch: UUID
    operation_id: UUID
    resource_id: UUID
    base_revision: ns.Revision | None = None

    @model_validator(mode="after")
    def revision_policy(self):
        create = self.kind.endswith(".create")
        if create != (self.base_revision is None):
            raise ValueError("Create requires null base_revision; mutations require a revision")
        return self


class FolderCreateOperation(Operation):
    kind: Literal["folder.create"]
    payload: ns.FolderCreate


class FolderUpdateOperation(Operation):
    kind: Literal["folder.update"]
    payload: FolderUpdate


class FolderDeleteOperation(Operation):
    kind: Literal["folder.delete"]
    payload: Empty


class NotebookCreateOperation(Operation):
    kind: Literal["notebook.create"]
    payload: ns.NotebookCreate


class NotebookUpdateOperation(Operation):
    kind: Literal["notebook.update"]
    payload: NotebookUpdate


class NotebookDeleteOperation(Operation):
    kind: Literal["notebook.delete"]
    payload: Empty


class TagCreateOperation(Operation):
    kind: Literal["tag.create"]
    payload: ns.TagCreate


class TagUpdateOperation(Operation):
    kind: Literal["tag.update"]
    payload: ns.TagCreate


class TagDeleteOperation(Operation):
    kind: Literal["tag.delete"]
    payload: Empty


class NoteCreateOperation(Operation):
    kind: Literal["note.create"]
    payload: ns.NoteCreate


class NoteUpdateOperation(Operation):
    kind: Literal["note.update"]
    payload: NoteUpdate


class NoteTrashOperation(Operation):
    kind: Literal["note.trash"]
    payload: Empty


class NoteRestoreOperation(Operation):
    kind: Literal["note.restore"]
    payload: Empty


class PageOrderOperation(Operation):
    kind: Literal["page.order"]
    payload: PageOrder


class PageRemoveOperation(Operation):
    kind: Literal["page.remove"]
    payload: PageRemove


class UploadCreateOperation(Operation):
    kind: Literal["upload.create"]
    payload: UploadCreate


class UploadCompleteOperation(Operation):
    kind: Literal["upload.complete"]
    payload: Empty


class PdfCreateOperation(Operation):
    kind: Literal["pdf.create"]
    payload: PdfCreate


class PdfStartOperation(Operation):
    kind: Literal["pdf.start"]
    payload: Empty


SyncIn = Annotated[
    FolderCreateOperation | FolderUpdateOperation | FolderDeleteOperation
    | NotebookCreateOperation | NotebookUpdateOperation | NotebookDeleteOperation
    | TagCreateOperation | TagUpdateOperation | TagDeleteOperation
    | NoteCreateOperation | NoteUpdateOperation | NoteTrashOperation | NoteRestoreOperation
    | PageOrderOperation | PageRemoveOperation | UploadCreateOperation | UploadCompleteOperation
    | PdfCreateOperation | PdfStartOperation,
    Field(discriminator="kind"),
]


class Transport(BaseModel):
    """Identifiers only; refresh capability URLs via existing upload-status API."""
    upload_id: UUID | None = None
    import_id: UUID | None = None
    file_id: UUID | None = None
    note_id: UUID | None = None
    page_id: UUID | None = None


class SyncOut(BaseModel):
    operation_id: UUID
    kind: str
    resource_id: UUID
    resource_mapping: dict[str, UUID]
    applied_revision: int | None
    transport: Transport


class PageState(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    note_id: UUID
    file_id: UUID
    position: int
    revision: int
    pdf_page_number: int | None


class ResourceState(BaseModel):
    resource_kind: Literal["folder", "notebook", "tag", "note", "page", "upload", "pdf"]
    exists: bool
    folder: ns.FolderOut | None = None
    notebook: ns.NotebookOut | None = None
    tag: ns.TagOut | None = None
    note: ns.NoteOut | None = None
    pages: list[PageState] = Field(default_factory=list)
    file: fs.FileOut | None = None
    status: str | None = None


class SyncConflict(BaseModel):
    code: str
    message: str
    request_id: str
    operation_id: UUID
    current: ResourceState | None = None


class SnapshotIn(Input):
    scope: Literal["all", "selected"]
    note_ids: list[UUID] = Field(default_factory=list, max_length=50_000)

    @model_validator(mode="after")
    def selection(self):
        if len(set(self.note_ids)) != len(self.note_ids) or (self.scope == "all" and self.note_ids):
            raise ValueError("Invalid note selection")
        return self


class PdfState(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    note_id: UUID
    source_file_id: UUID
    status: str
    page_count: int | None
    completed_pages: int
    error_code: str | None


class AssetState(BaseModel):
    file_id: UUID
    kind: Literal["original", "reading", "thumbnail"]
    mime: str
    bytes: int | None
    sha256: str | None
    available: bool


class SnapshotOut(BaseModel):
    version: Literal[1] = 1
    snapshot_id: UUID
    content_version: str
    owner_id: UUID
    sync_epoch: UUID
    created_at: datetime
    scope: Literal["all", "selected"]
    folders: list[ns.FolderOut]
    notebooks: list[ns.NotebookOut]
    tags: list[ns.TagOut]
    notes: list[ns.NoteOut]
    pages: list[PageState]
    pdf_imports: list[PdfState]
    files: list[fs.FileOut]
    assets: list[AssetState]
    total_rows: int
    metadata_bytes: int
    asset_bytes: int
