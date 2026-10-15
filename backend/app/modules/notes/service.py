from uuid import UUID
from sqlalchemy import select, update, delete, func, tuple_
from app.shared.db import utcnow
from app.shared.errors import AppError
from app.shared.permissions import require_owner
from app.modules.notes.models import Folder, Notebook, Tag, Note, NoteTag, NoteVersion
from app.modules.notes import schemas as s
from app.modules.notes import cursors


def owned(db, user, model, resource_id, *, deleted=False):
    row = require_owner(user, db.get(model, resource_id))
    if isinstance(row, Note) and row.deleted_at is not None and not deleted:
        raise AppError(404, "not_found", "笔记不存在或无权访问")
    return row


def revision_matches(row, revision):
    if row.revision != revision:
        raise AppError(409, "revision_conflict", "内容已更新，请保留草稿并重新加载")


def revise(db, row, expected, changes):
    # current_user locks the owner's user row for every write. In addition, the
    # UPDATE itself checks revision so no stale writer can overwrite a resource.
    result = db.scalar(update(type(row)).where(type(row).id == row.id, type(row).owner_id == row.owner_id, type(row).revision == expected).values(**changes, revision=expected + 1, updated_at=utcnow()).returning(type(row)))
    if result is None:
        raise AppError(409, "revision_conflict", "内容已更新，请保留草稿并重新加载")
    return result


def folder_parent(db, user, parent_id, moving_id=None):
    cursor = parent_id
    visited = set()
    while cursor is not None:
        if cursor == moving_id or cursor in visited:
            raise AppError(409, "folder_cycle", "文件夹不能移动到自身或后代中")
        visited.add(cursor)
        cursor = owned(db, user, Folder, cursor).parent_id


def tag_name(db, user, name, excluding=None):
    query = select(Tag.id).where(Tag.owner_id == user.id, Tag.name == name)
    if excluding:
        query = query.where(Tag.id != excluding)
    if db.scalar(query):
        raise AppError(409, "tag_exists", "标签名称已存在")


def validate_note_relations(db, user, notebook_id, tag_ids):
    if notebook_id is not None:
        owned(db, user, Notebook, notebook_id)
    if tag_ids:
        valid = db.scalars(select(Tag.id).where(Tag.owner_id == user.id, Tag.id.in_(tag_ids))).all()
        if set(valid) != set(tag_ids):
            raise AppError(404, "not_found", "标签不存在或无权访问")


def tags_for(db, owner_id, note_ids):
    grouped = {note_id: [] for note_id in note_ids}
    if note_ids:
        for note_id, tag_id in db.execute(select(NoteTag.note_id, NoteTag.tag_id).where(NoteTag.owner_id == owner_id, NoteTag.note_id.in_(note_ids)).order_by(NoteTag.tag_id)):
            grouped[note_id].append(tag_id)
    return grouped


def note_output(db, note):
    result = s.NoteOut.model_validate({**{key: getattr(note, key) for key in s.NoteOut.model_fields if key != "tag_ids"}, "tag_ids": tags_for(db, note.owner_id, [note.id])[note.id]})
    return result


def replace_tags(db, note, tag_ids):
    db.execute(delete(NoteTag).where(NoteTag.note_id == note.id, NoteTag.owner_id == note.owner_id))
    db.add_all(NoteTag(owner_id=note.owner_id, note_id=note.id, tag_id=tag_id) for tag_id in tag_ids)
    db.flush()


def save_snapshot(db, note):
    snapshot = note_output(db, note).model_dump(mode="json")
    if note.kind == "image":
        from app.modules.files.models import NotePage
        snapshot["pages"] = [
            {"id": str(page.id), "file_id": str(page.file_id), "position": page.position,
             "revision": page.revision}
            for page in db.scalars(select(NotePage).where(NotePage.note_id == note.id,
                                    NotePage.owner_id == note.owner_id).order_by(NotePage.position))
        ]
    db.add(NoteVersion(note_id=note.id, owner_id=note.owner_id, revision=note.revision, snapshot=snapshot))


def new_note(db, user, data):
    validate_note_relations(db, user, data.notebook_id, data.tag_ids)
    note = Note(owner_id=user.id, **data.model_dump(exclude={"tag_ids"}))
    db.add(note)
    db.flush()
    replace_tags(db, note, data.tag_ids)
    save_snapshot(db, note)
    return note


def update_note(db, user, note_id, data):
    note = owned(db, user, Note, note_id)
    revision_matches(note, data.revision)
    changes = data.model_dump(exclude_unset=True, exclude={"revision", "tag_ids"})
    try:
        s.validate_body(changes.get("body_json", note.body_json), changes.get("plain_text", note.plain_text))
    except (ValueError, TypeError, UnicodeError):
        raise AppError(422, "note_body_invalid", "正文格式或大小超出限制")
    if note.kind == "image" and (changes.get("body_json") or changes.get("plain_text")):
        raise AppError(422, "image_note_body", "图片笔记不支持富文本正文")
    validate_note_relations(db, user, changes.get("notebook_id"), data.tag_ids)
    note = revise(db, note, data.revision, changes)
    if "tag_ids" in data.model_fields_set:
        replace_tags(db, note, data.tag_ids)
    save_snapshot(db, note)
    return note


def list_notes(db, user, *, offset, limit, q, kind, tag_id, notebook_id, folder_id, deleted, cursor=None):
    if q is not None:
        try:
            s.validate_pg_text(q)
        except ValueError:
            raise AppError(422, "query_invalid", "筛选文本格式不正确") from None
    scope = cursors.scope_key(user.id, q, kind, tag_id, notebook_id, folder_id, deleted)
    cutoff, anchor = utcnow(), None
    if cursor is not None:
        if offset:
            raise cursors.invalid_cursor()
        cutoff, at, note_id = cursors.note_position(cursor, scope)
        anchor = (at, note_id)
    predicates = [Note.owner_id == user.id, Note.deleted_at.is_not(None) if deleted else Note.deleted_at.is_(None)]
    # A cutoff prevents notes created/edited after the first page from being
    # inserted into later pages. This is a live list, not a historical snapshot:
    # an unseen note updated after cutoff will appear on the next refresh.
    predicates.append(Note.updated_at <= cutoff)
    if q:
        # Treat % and _ as literal characters rather than exposing LIKE wildcards.
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        predicates.append(Note.title.ilike(pattern, escape="\\") | Note.plain_text.ilike(pattern, escape="\\"))
    if kind:
        predicates.append(Note.kind == kind)
    if notebook_id:
        predicates.append(Note.notebook_id == notebook_id)
    if folder_id:
        notebooks = select(Notebook.id).where(Notebook.owner_id == user.id, Notebook.folder_id == folder_id)
        predicates.append(Note.notebook_id.in_(notebooks))
    if tag_id:
        notes = select(NoteTag.note_id).where(NoteTag.owner_id == user.id, NoteTag.tag_id == tag_id)
        predicates.append(Note.id.in_(notes))
    total = db.scalar(select(func.count()).select_from(Note).where(*predicates))
    query = select(Note).where(*predicates)
    if anchor:
        query = query.where(tuple_(Note.updated_at, Note.id) < anchor)
    rows = db.scalars(query.order_by(Note.updated_at.desc(), Note.id.desc()).offset(offset).limit(limit + 1)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more:
        last = rows[-1]
        next_cursor = cursors.encode_cursor({"v": 1, "scope": scope, "cutoff": cutoff.isoformat(), "at": last.updated_at.isoformat(), "id": str(last.id)})
    tags = tags_for(db, user.id, [row.id for row in rows])
    items = [{**{key: getattr(row, key) for key in s.NoteSummary.model_fields if key != "tag_ids"}, "tag_ids": tags[row.id]} for row in rows]
    return {"items": items, "total": total, "offset": offset, "limit": limit, "next_cursor": next_cursor}


def list_versions(db, user, note_id, limit, cursor):
    note = owned(db, user, Note, note_id, deleted=True)
    scope = cursors.scope_key(user.id, note_id, "versions")
    cutoff, before = note.revision, None
    if cursor is not None:
        cutoff, before = cursors.version_position(cursor, scope)
        if cutoff > note.revision:
            raise cursors.invalid_cursor()
    predicates = [NoteVersion.owner_id == user.id, NoteVersion.note_id == note_id, NoteVersion.revision <= cutoff]
    total = db.scalar(select(func.count()).select_from(NoteVersion).where(*predicates))
    query = select(NoteVersion).where(*predicates)
    if before is not None:
        query = query.where(NoteVersion.revision < before)
    rows = db.scalars(query.order_by(NoteVersion.revision.desc()).limit(limit + 1)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = cursors.encode_cursor({"v": 1, "scope": scope, "cutoff": cutoff, "before": rows[-1].revision}) if has_more else None
    return {"items": rows, "total": total, "limit": limit, "next_cursor": next_cursor}


def remove_organization(db, user, model, resource_id, revision):
    row = owned(db, user, model, resource_id)
    revision_matches(row, revision)
    if model is Folder:
        occupied = db.scalar(select(Folder.id).where(Folder.owner_id == user.id, Folder.parent_id == row.id).limit(1)) or db.scalar(select(Notebook.id).where(Notebook.owner_id == user.id, Notebook.folder_id == row.id).limit(1))
    elif model is Notebook:
        # Deleted notes retain their notebook so restoring does not silently lose organization.
        occupied = db.scalar(select(Note.id).where(Note.owner_id == user.id, Note.notebook_id == row.id).limit(1))
    else:
        occupied = db.scalar(select(NoteTag.note_id).where(NoteTag.owner_id == user.id, NoteTag.tag_id == row.id).limit(1))
    if occupied:
        raise AppError(409, "resource_not_empty", "请先移动或解除关联内容后再删除")
    deleted = db.scalar(delete(model).where(model.id == row.id, model.owner_id == user.id, model.revision == revision).returning(model.id))
    if deleted is None:
        raise AppError(409, "revision_conflict", "内容已更新，请重新加载")
