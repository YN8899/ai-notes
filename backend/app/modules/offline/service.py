"""One operation, one business transaction, one permanent success receipt."""
import hashlib
import json

from sqlalchemy import select

from app.modules.files import schemas as fs, service as files, pdf_service as pdf
from app.modules.files.models import File, NotePage, PdfImport, UploadSession
from app.modules.files.oss_storage import StorageUnavailable
from app.modules.notes import schemas as ns, service as notes
from app.modules.notes.models import Folder, Note, Notebook, Tag
from app.modules.offline import schemas as s
from app.modules.offline.models import OfflineSyncReceipt, OfflineSyncState
from app.modules.offline.validation import validate_upload
from app.shared.config import settings
from app.shared.db import utcnow
from app.shared.errors import AppError
from app.shared.permissions import require_owner


ORGANIZATION = {"folder": Folder, "notebook": Notebook, "tag": Tag}
OUTPUT = {"folder": ns.FolderOut, "notebook": ns.NotebookOut, "tag": ns.TagOut}


def ensure_state(db, user):
    """Called only by snapshot, with current_user's owner mutation lock held.

    Never seed the server generation from an untrusted client operation. Epochs
    are instance generation identifiers, not credentials or authentication tokens.
    """
    state = db.get(OfflineSyncState, user.id)
    if state is None:
        state = OfflineSyncState(owner_id=user.id)
        db.add(state)
        db.flush()
    return state


def fingerprint(operation):
    # Include omitted-vs-explicit-null for patch fields. Equivalent UUID spellings
    # are normalized by schema validation before entering the receipt boundary.
    data = operation.model_dump(mode="json")
    data["payload"] = operation.payload.model_dump(mode="json", exclude_unset=True)
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def receipt_output(row):
    return s.SyncOut(operation_id=row.operation_id, kind=row.kind, resource_id=row.resource_id,
                     resource_mapping=row.resource_mapping, applied_revision=row.applied_revision,
                     transport=s.Transport.model_validate(row.transport))


def _available_id(db, model, resource_id):
    if db.get(model, resource_id) is not None:
        # No owner, role, existence detail or foreign content is disclosed.
        raise AppError(409, "resource_conflict", "资源标识不可用于创建，请保留本机内容并使用新标识")


def _read_upload(db, upload, max_bytes):
    file = db.get(File, upload.file_id)
    if upload.status not in {"received", "completed"}:
        raise AppError(409, "upload_incomplete", "文件尚未上传完成")
    try:
        info = files.storage().head(file.object_key)
        if info.size != file.expected_bytes or info.size > max_bytes:
            raise AppError(422, "upload_mismatch", "文件实际大小与声明不一致")
        data = files.storage().read(file.object_key)
    except FileNotFoundError:
        raise AppError(409, "upload_incomplete", "上传文件未找到，请重试") from None
    except StorageUnavailable:
        raise AppError(503, "storage_unavailable", "文件存储暂不可用，请稍后重试") from None
    if len(data) != file.expected_bytes or len(data) > max_bytes:
        raise AppError(422, "upload_mismatch", "文件实际大小与声明不一致")
    return file, data


def _validate_image(db, user, upload_id):
    upload = files.owned_upload(db, user, upload_id)
    cfg = settings()
    file, data = _read_upload(db, upload, cfg.upload_max_bytes)
    processed = validate_upload("image", data, mime=file.mime, max_pixels=cfg.upload_max_pixels,
                                timeout=min(30, cfg.upload_read_timeout_seconds))
    # Original's immutable hash is available to offline cache before the worker
    # creates display variants; the worker still performs its usual validation.
    file.sha256 = processed["sha256"]
    return upload


def _validate_pdf(db, user, import_id):
    item = pdf.owned(db, user, import_id)
    upload = db.scalar(select(UploadSession).where(UploadSession.file_id == item.source_file_id))
    files.owned_upload(db, user, upload.id)
    cfg = settings()
    file, data = _read_upload(db, upload, cfg.pdf_max_bytes)
    if file.mime != "application/pdf" or not data.startswith(b"%PDF-"):
        raise AppError(422, "pdf_invalid", "PDF 未通过内容校验")
    info = validate_upload("pdf", data, max_pages=cfg.pdf_max_pages,
                           timeout=cfg.pdf_page_timeout_seconds, max_pixels=cfg.upload_max_pixels)
    item.page_count = info["page_count"]
    file.sha256 = hashlib.sha256(data).hexdigest()
    return item


def _note(db, user, note_id, revision, *, deleted=False):
    note = notes.owned(db, user, Note, note_id, deleted=deleted)
    notes.revision_matches(note, revision)
    return note


def apply(db, user, operation):
    """Do not commit here. The caller revalidates auth and commits once."""
    state = db.get(OfflineSyncState, user.id)
    if state is None or state.epoch != operation.sync_epoch:
        # This precedes receipt lookup too: an old vault must not receive an old
        # success as authority to continue its queue in a new restored instance.
        raise AppError(409, "offline_epoch_changed", "服务器离线同步代次已变化，请先获取新快照并保留本机未同步内容")
    request_hash = fingerprint(operation)
    receipt = db.scalar(select(OfflineSyncReceipt).where(
        OfflineSyncReceipt.owner_id == user.id, OfflineSyncReceipt.operation_id == operation.operation_id))
    if receipt:
        if receipt.request_fingerprint != request_hash:
            raise AppError(409, "operation_conflict", "同一操作标识不能用于不同内容，请保留原操作")
        return receipt_output(receipt)
    kind, action = operation.kind.split(".")
    rid, rev, data = operation.resource_id, operation.base_revision, operation.payload
    result_id, mapping, applied, transport = rid, {}, None, {}

    if kind in ORGANIZATION:
        model = ORGANIZATION[kind]
        changes = data.model_dump(exclude_unset=True)
        if action == "create":
            _available_id(db, model, rid)
            if kind == "folder":
                notes.folder_parent(db, user, data.parent_id)
            elif kind == "notebook" and data.folder_id:
                notes.owned(db, user, Folder, data.folder_id)
            elif kind == "tag":
                notes.tag_name(db, user, data.name)
            row = model(id=rid, owner_id=user.id, **data.model_dump())
            db.add(row)
            db.flush()
            applied = row.revision
        elif action == "delete":
            notes.remove_organization(db, user, model, rid, rev)
            applied = rev
        else:
            row = notes.owned(db, user, model, rid)
            notes.revision_matches(row, rev)
            if kind == "folder" and "parent_id" in data.model_fields_set:
                notes.folder_parent(db, user, data.parent_id, rid)
            elif kind == "notebook" and data.folder_id:
                notes.owned(db, user, Folder, data.folder_id)
            elif kind == "tag":
                notes.tag_name(db, user, data.name, rid)
            row = notes.revise(db, row, rev, changes)
            applied = row.revision
    elif kind == "note":
        if action == "create":
            _available_id(db, Note, rid)
            notes.validate_note_relations(db, user, data.notebook_id, data.tag_ids)
            # Assign the final client UUID before any flush, tags or snapshots.
            row = Note(id=rid, owner_id=user.id, **data.model_dump(exclude={"tag_ids"}))
            db.add(row)
            db.flush()
            notes.replace_tags(db, row, data.tag_ids)
            notes.save_snapshot(db, row)
        elif action == "update":
            current = _note(db, user, rid, rev, deleted=True)
            if current.deleted_at is not None:
                raise AppError(409, "note_deleted", "笔记已在回收站，请先选择恢复或保留本机副本")
            row = notes.update_note(db, user, rid, ns.NotePatch(revision=rev, **data.model_dump(exclude_unset=True)))
        else:
            row = _note(db, user, rid, rev, deleted=True)
            if action == "restore" and row.deleted_at is None:
                raise AppError(409, "note_not_deleted", "笔记未被删除")
            if action == "trash" and row.deleted_at is not None:
                raise AppError(409, "note_deleted", "笔记已在回收站")
            row = notes.revise(db, row, rev, {"deleted_at": None if action == "restore" else utcnow()})
            notes.save_snapshot(db, row)
        applied, transport = row.revision, {"note_id": str(row.id)}
    elif kind == "page":
        if action == "order":
            applied = files.reorder_pages(db, user, rid, fs.ReorderIn(revision=rev, page_ids=data.page_ids))
            transport = {"note_id": str(rid)}
        else:
            note = _note(db, user, data.note_id, rev)
            files.image_note(db, user, note.id)
            pdf.require_idle(db, note.id)
            page = require_owner(user, db.get(NotePage, rid))
            if page.note_id != note.id:
                raise AppError(404, "not_found", "页面不存在或无权访问")
            if page.deleted_at is not None:
                raise AppError(409, "page_already_removed", "页面已移除，请重新加载当前页面")
            page.deleted_at = utcnow()
            page.revision += 1
            db.flush()
            # Keep File and NoteVersion references intact. A gap in positions is
            # legal; the next reorder uses the existing safe two-phase move.
            note = notes.revise(db, note, rev, {})
            notes.save_snapshot(db, note)
            applied, transport = note.revision, {"note_id": str(note.id), "page_id": str(rid)}
    elif kind == "upload":
        if action == "create":
            _available_id(db, UploadSession, rid)
            upload = files.create_upload(db, user, data.note_id, fs.UploadCreate(
                **data.model_dump(exclude={"note_id"}), request_key=f"offline_{operation.operation_id.hex}"))
            result_id = upload.id
            mapping[str(rid)] = str(upload.id)
            note = db.get(Note, upload.note_id)
            applied = note.revision
            transport = {"upload_id": str(upload.id), "file_id": str(upload.file_id), "note_id": str(note.id)}
        else:
            upload = files.owned_upload(db, user, rid)
            _note(db, user, upload.note_id, rev)
            _validate_image(db, user, rid)
            page, applied = files.complete_upload(db, user, rid, rev)
            transport = {"upload_id": str(rid), "file_id": str(upload.file_id),
                         "note_id": str(upload.note_id), "page_id": str(page.id)}
    elif kind == "pdf":
        if action == "create":
            _available_id(db, Note, rid)
            item = pdf.create(db, user, fs.PdfImportCreate(**data.model_dump(),
                              request_key=f"offline_{operation.operation_id.hex}"))
            result_id = item.note_id
            mapping[str(rid)] = str(item.note_id)
        else:
            item = pdf.owned(db, user, rid)
            _note(db, user, item.note_id, rev)
            if item.status != "uploading":
                raise AppError(409, "pdf_already_started", "PDF 已开始转换，请重新加载当前状态")
            _validate_pdf(db, user, rid)
            item = pdf.start(db, user, rid)
        upload = db.scalar(select(UploadSession).where(UploadSession.file_id == item.source_file_id))
        applied = db.get(Note, item.note_id).revision
        transport = {"import_id": str(item.id), "upload_id": str(upload.id),
                     "file_id": str(item.source_file_id), "note_id": str(item.note_id)}
    db.flush()
    receipt = OfflineSyncReceipt(owner_id=user.id, operation_id=operation.operation_id,
        request_fingerprint=request_hash, kind=operation.kind, resource_id=result_id,
        resource_mapping=mapping, applied_revision=applied, transport=transport)
    db.add(receipt)
    db.flush()  # A receipt failure rolls back all business mutations as well.
    return receipt_output(receipt)


def current_state(db, user, operation):
    """Only owner-visible state is returned, including trash and full note body."""
    kind = operation.kind.split(".")[0]
    rid = operation.resource_id
    if operation.kind.endswith(".create"):
        return None
    state = s.ResourceState(resource_kind=kind, exists=False)
    if kind in ORGANIZATION:
        row = db.get(ORGANIZATION[kind], rid)
        if row and row.owner_id == user.id:
            state.exists = True
            setattr(state, kind, OUTPUT[kind].model_validate(row))
        return state
    if kind == "page":
        if operation.kind == "page.order":
            note_id = rid
        else:
            note_id = operation.payload.note_id
    elif kind == "note":
        note_id = rid
    else:
        model = UploadSession if kind == "upload" else PdfImport
        item = db.get(model, rid)
        if not item or item.owner_id != user.id:
            return state
        note_id = item.note_id
        state.status = item.status
        file = db.get(File, item.file_id if kind == "upload" else item.source_file_id)
        state.file = fs.FileOut.model_validate(file)
    note = db.get(Note, note_id)
    if not note or note.owner_id != user.id:
        return state
    state.exists = True
    state.note = notes.note_output(db, note)
    state.pages = [s.PageState.model_validate(page) for page in db.scalars(select(NotePage).where(
        NotePage.note_id == note.id, NotePage.owner_id == user.id, NotePage.deleted_at.is_(None)).order_by(NotePage.position))]
    return state
