from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Query, Response
from sqlalchemy import select
from app.shared.dependencies import DB, CurrentUser
from app.shared.db import utcnow
from app.shared.errors import AppError
from app.modules.notes.models import Folder, Notebook, Tag, Note, NoteVersion
from app.modules.notes import service, schemas as s

router = APIRouter(tags=["notes"])


@router.get("/folders", response_model=list[s.FolderOut])
def folders(db: DB, user: CurrentUser):
    return db.scalars(select(Folder).where(Folder.owner_id == user.id).order_by(Folder.created_at, Folder.id)).all()


@router.post("/folders", response_model=s.FolderOut, status_code=201)
def create_folder(data: s.FolderCreate, db: DB, user: CurrentUser):
    service.folder_parent(db, user, data.parent_id)
    row = Folder(owner_id=user.id, **data.model_dump())
    db.add(row)
    db.commit()
    return row


@router.get("/folders/{folder_id}", response_model=s.FolderOut)
def folder(folder_id: UUID, db: DB, user: CurrentUser):
    return service.owned(db, user, Folder, folder_id)


@router.patch("/folders/{folder_id}", response_model=s.FolderOut)
def update_folder(folder_id: UUID, data: s.FolderPatch, db: DB, user: CurrentUser):
    row = service.owned(db, user, Folder, folder_id)
    service.revision_matches(row, data.revision)
    if "parent_id" in data.model_fields_set:
        service.folder_parent(db, user, data.parent_id, row.id)
    row = service.revise(db, row, data.revision, data.model_dump(exclude_unset=True, exclude={"revision"}))
    db.commit()
    return row


@router.delete("/folders/{folder_id}", status_code=204)
def delete_folder(folder_id: UUID, data: s.RevisionIn, db: DB, user: CurrentUser):
    service.remove_organization(db, user, Folder, folder_id, data.revision)
    db.commit()
    return Response(status_code=204)


@router.get("/notebooks", response_model=list[s.NotebookOut])
def notebooks(db: DB, user: CurrentUser, folder_id: UUID | None = None):
    query = select(Notebook).where(Notebook.owner_id == user.id)
    if folder_id:
        query = query.where(Notebook.folder_id == folder_id)
    return db.scalars(query.order_by(Notebook.created_at, Notebook.id)).all()


@router.post("/notebooks", response_model=s.NotebookOut, status_code=201)
def create_notebook(data: s.NotebookCreate, db: DB, user: CurrentUser):
    if data.folder_id:
        service.owned(db, user, Folder, data.folder_id)
    row = Notebook(owner_id=user.id, **data.model_dump())
    db.add(row)
    db.commit()
    return row


@router.get("/notebooks/{notebook_id}", response_model=s.NotebookOut)
def notebook(notebook_id: UUID, db: DB, user: CurrentUser):
    return service.owned(db, user, Notebook, notebook_id)


@router.patch("/notebooks/{notebook_id}", response_model=s.NotebookOut)
def update_notebook(notebook_id: UUID, data: s.NotebookPatch, db: DB, user: CurrentUser):
    row = service.owned(db, user, Notebook, notebook_id)
    service.revision_matches(row, data.revision)
    if data.folder_id:
        service.owned(db, user, Folder, data.folder_id)
    row = service.revise(db, row, data.revision, data.model_dump(exclude_unset=True, exclude={"revision"}))
    db.commit()
    return row


@router.delete("/notebooks/{notebook_id}", status_code=204)
def delete_notebook(notebook_id: UUID, data: s.RevisionIn, db: DB, user: CurrentUser):
    service.remove_organization(db, user, Notebook, notebook_id, data.revision)
    db.commit()
    return Response(status_code=204)


@router.get("/tags", response_model=list[s.TagOut])
def tags(db: DB, user: CurrentUser):
    return db.scalars(select(Tag).where(Tag.owner_id == user.id).order_by(Tag.name, Tag.id)).all()


@router.post("/tags", response_model=s.TagOut, status_code=201)
def create_tag(data: s.TagCreate, db: DB, user: CurrentUser):
    service.tag_name(db, user, data.name)
    row = Tag(owner_id=user.id, name=data.name)
    db.add(row)
    db.commit()
    return row


@router.get("/tags/{tag_id}", response_model=s.TagOut)
def tag(tag_id: UUID, db: DB, user: CurrentUser):
    return service.owned(db, user, Tag, tag_id)


@router.patch("/tags/{tag_id}", response_model=s.TagOut)
def update_tag(tag_id: UUID, data: s.TagPatch, db: DB, user: CurrentUser):
    row = service.owned(db, user, Tag, tag_id)
    service.revision_matches(row, data.revision)
    service.tag_name(db, user, data.name, row.id)
    row = service.revise(db, row, data.revision, {"name": data.name})
    db.commit()
    return row


@router.delete("/tags/{tag_id}", status_code=204)
def delete_tag(tag_id: UUID, data: s.RevisionIn, db: DB, user: CurrentUser):
    service.remove_organization(db, user, Tag, tag_id, data.revision)
    db.commit()
    return Response(status_code=204)


@router.get("/notes", response_model=s.NoteList)
def notes(db: DB, user: CurrentUser, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100), q: s.SearchText | None = Query(None, max_length=200), kind: Literal["text", "image"] | None = None, tag_id: UUID | None = None, notebook_id: UUID | None = None, folder_id: UUID | None = None, deleted: bool = False, cursor: str | None = Query(None, max_length=1024)):
    return service.list_notes(db, user, offset=offset, limit=limit, q=q, kind=kind, tag_id=tag_id, notebook_id=notebook_id, folder_id=folder_id, deleted=deleted, cursor=cursor)


@router.post("/notes", response_model=s.NoteOut, status_code=201)
def create_note(data: s.NoteCreate, db: DB, user: CurrentUser):
    row = service.new_note(db, user, data)
    db.commit()
    return service.note_output(db, row)


@router.get("/notes/{note_id}", response_model=s.NoteOut)
def note(note_id: UUID, db: DB, user: CurrentUser):
    return service.note_output(db, service.owned(db, user, Note, note_id))


@router.patch("/notes/{note_id}", response_model=s.NoteOut)
def update_note(note_id: UUID, data: s.NotePatch, db: DB, user: CurrentUser):
    row = service.update_note(db, user, note_id, data)
    db.commit()
    return service.note_output(db, row)


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(note_id: UUID, data: s.RevisionIn, db: DB, user: CurrentUser):
    row = service.owned(db, user, Note, note_id)
    service.revision_matches(row, data.revision)
    row = service.revise(db, row, data.revision, {"deleted_at": utcnow()})
    service.save_snapshot(db, row)
    db.commit()
    return Response(status_code=204)


@router.post("/notes/{note_id}/restore", response_model=s.NoteOut)
def restore_note(note_id: UUID, data: s.RevisionIn, db: DB, user: CurrentUser):
    row = service.owned(db, user, Note, note_id, deleted=True)
    service.revision_matches(row, data.revision)
    if row.deleted_at is None:
        raise AppError(409, "note_not_deleted", "笔记未被删除")
    row = service.revise(db, row, data.revision, {"deleted_at": None})
    service.save_snapshot(db, row)
    db.commit()
    return service.note_output(db, row)


@router.get("/notes/{note_id}/versions", response_model=list[s.VersionOut])
def note_versions(note_id: UUID, db: DB, user: CurrentUser, limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
    service.owned(db, user, Note, note_id, deleted=True)
    return db.scalars(select(NoteVersion).where(NoteVersion.owner_id == user.id, NoteVersion.note_id == note_id).order_by(NoteVersion.revision.desc()).offset(offset).limit(limit)).all()


@router.get("/notes/{note_id}/versions/page", response_model=s.VersionList)
def note_versions_page(note_id: UUID, db: DB, user: CurrentUser, limit: int = Query(30, ge=1, le=100), cursor: str | None = Query(None, max_length=1024)):
    return service.list_versions(db, user, note_id, limit, cursor)
