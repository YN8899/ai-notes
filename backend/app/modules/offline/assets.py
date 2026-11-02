import hashlib

from sqlalchemy import select

from app.modules.files import service as files
from app.modules.files.models import File, FileVariant, NotePage, PdfImport
from app.modules.files.oss_storage import StorageUnavailable
from app.modules.notes.models import Note
from app.shared.config import settings
from app.shared.errors import AppError
from app.shared.permissions import require_owner


def read(db, user, file_id, kind):
    file = require_owner(user, db.get(File, file_id))
    # Include trash deliberately, but require a CURRENT owner-private relation.
    # File ownership alone, historical versions, shares and community are not grants.
    linked = db.scalar(select(NotePage.id).join(Note, Note.id == NotePage.note_id).where(
        NotePage.owner_id == user.id, Note.owner_id == user.id,
        NotePage.file_id == file_id, NotePage.deleted_at.is_(None)).limit(1))
    if not linked:
        linked = db.scalar(select(PdfImport.id).join(Note, Note.id == PdfImport.note_id).where(
            PdfImport.owner_id == user.id, Note.owner_id == user.id,
            PdfImport.source_file_id == file_id).limit(1))
    if not linked:
        raise AppError(404, "not_found", "文件不存在或无权访问")
    if file.mime not in {"image/jpeg", "image/png", "image/webp", "application/pdf"}:
        raise AppError(409, "offline_asset_invalid", "离线文件元信息不完整")
    if file.status not in {"ready", "processing"} or (kind != "original" and file.status != "ready"):
        raise AppError(409, "file_not_ready", "文件尚未通过校验或处理尚未完成")
    key, mime, size, expected_hash = file.object_key, file.mime, file.bytes, file.sha256
    if kind != "original":
        if file.mime == "application/pdf":
            raise AppError(422, "pdf_original_only", "PDF 原件仅支持下载原文件")
        variant = db.scalar(select(FileVariant).where(FileVariant.file_id == file_id, FileVariant.kind == kind))
        if not variant:
            raise AppError(409, "offline_asset_missing", "离线文件缺失，请稍后重试")
        key, mime, size, expected_hash = variant.object_key, "image/jpeg", variant.bytes, None
    limit = settings().pdf_max_bytes if mime == "application/pdf" else settings().upload_max_bytes
    if size is None or not 0 < size <= limit:
        raise AppError(409, "offline_asset_invalid", "离线文件元信息不完整")
    try:
        if files.storage().head(key).size != size:
            raise AppError(409, "offline_asset_invalid", "离线文件未通过完整性检查")
        data = files.storage().read(key)
    except FileNotFoundError:
        raise AppError(409, "offline_asset_missing", "离线文件缺失，请稍后重试") from None
    except StorageUnavailable:
        raise AppError(503, "storage_unavailable", "文件存储暂不可用，请稍后重试") from None
    actual_hash = hashlib.sha256(data).hexdigest()
    if len(data) != size or (expected_hash is not None and expected_hash != actual_hash):
        raise AppError(409, "offline_asset_invalid", "离线文件未通过完整性检查")
    # Older ready PDFs can lack a hash until rendering has started. Return the
    # actual content hash, never a hash inferred solely from metadata.
    if mime == "application/pdf":
        if not data.startswith(b"%PDF-"):
            raise AppError(409, "offline_asset_invalid", "离线文件未通过完整性检查")
        from app.modules.offline.validation import validate_upload
        validate_upload("pdf", data, max_pixels=settings().upload_max_pixels,
                        max_pages=settings().pdf_max_pages, timeout=settings().pdf_page_timeout_seconds)
    else:
        # Variants have no persisted hash in the legacy schema. Decode them in
        # the same disposable boundary before caching, then report actual SHA.
        from app.modules.offline.validation import validate_upload
        validate_upload("image", data, mime=mime, max_pixels=settings().upload_max_pixels,
                        timeout=min(30, settings().upload_read_timeout_seconds))
    return data, mime, actual_hash
