"""A bounded consistent snapshot of exactly one owner's private notebook space."""
import hashlib
import time
from uuid import uuid4

from sqlalchemy import select, text, union

from app.modules.files import schemas as fs
from app.modules.files.models import File, FileVariant, NotePage, PdfImport
from app.modules.notes import schemas as ns, service as notes
from app.modules.notes.models import Folder, Note, Notebook, Tag
from app.modules.offline import schemas as s
from app.modules.offline.models import OfflineSyncState
from app.shared.config import settings
from app.shared.db import SessionLocal, utcnow
from app.shared.errors import AppError


def _large():
    return AppError(413, "offline_snapshot_too_large", "离线资料超过本次缓存上限，请减少所选笔记")


def create(owner_id, data):
    cfg = settings()
    deadline = time.monotonic() + cfg.export_max_seconds
    total_rows, metadata_bytes = 0, 0

    def checkpoint():
        if time.monotonic() >= deadline:
            raise AppError(503, "offline_snapshot_timeout", "离线资料生成超时，请稍后重试")

    def collect(db, query, schema):
        nonlocal total_rows, metadata_bytes
        rows = []
        for row in db.scalars(query.execution_options(yield_per=100)):
            checkpoint()
            total_rows += 1
            if total_rows > cfg.export_max_rows:
                raise _large()
            value = schema(row)
            metadata_bytes += len(value.model_dump_json().encode())
            if metadata_bytes > cfg.export_max_metadata_bytes:
                raise _large()
            rows.append(value)
        return rows

    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        db.execute(text(f"SET LOCAL statement_timeout = {max(1, int(cfg.export_max_seconds * 1000))}"))
        sync_state = db.get(OfflineSyncState, owner_id)
        if sync_state is None:
            raise AppError(409, "offline_epoch_changed", "请重新获取离线快照后同步")
        # Any queries performed by the schema conversion share this transaction.
        folders = collect(db, select(Folder).where(Folder.owner_id == owner_id).order_by(Folder.id), ns.FolderOut.model_validate)
        notebooks = collect(db, select(Notebook).where(Notebook.owner_id == owner_id).order_by(Notebook.id), ns.NotebookOut.model_validate)
        tags = collect(db, select(Tag).where(Tag.owner_id == owner_id).order_by(Tag.id), ns.TagOut.model_validate)
        note_query = select(Note).where(Note.owner_id == owner_id)
        if data.scope == "selected":
            note_query = note_query.where(Note.id.in_(data.note_ids))
        note_ids = note_query.with_only_columns(Note.id)
        note_values = collect(db, note_query.order_by(Note.id), lambda row: notes.note_output(db, row))
        if data.scope == "selected" and {note.id for note in note_values} != set(data.note_ids):
            raise AppError(404, "not_found", "所选笔记不存在或无权访问")
        page_query = select(NotePage).where(NotePage.owner_id == owner_id, NotePage.note_id.in_(note_ids),
                                            NotePage.deleted_at.is_(None))
        pdf_query = select(PdfImport).where(PdfImport.owner_id == owner_id, PdfImport.note_id.in_(note_ids))
        pages = collect(db, page_query.order_by(NotePage.note_id, NotePage.position), s.PageState.model_validate)
        pdfs = collect(db, pdf_query.order_by(PdfImport.id), s.PdfState.model_validate)
        file_ids = union(page_query.with_only_columns(NotePage.file_id), pdf_query.with_only_columns(PdfImport.source_file_id))
        file_rows = collect(db, select(File).where(File.owner_id == owner_id, File.id.in_(file_ids)).order_by(File.id), fs.FileOut.model_validate)
        # Only the whitelisted public columns above cross the snapshot boundary.
        # SHA/byte values below are selected separately; object keys never are.
        hashes = {str(file_id): (sha, byte_count) for file_id, sha, byte_count in db.execute(
            select(File.id, File.sha256, File.bytes).where(File.owner_id == owner_id, File.id.in_(file_ids)))}
        variant_values = {}
        for file_id, kind, byte_count in db.execute(select(FileVariant.file_id, FileVariant.kind, FileVariant.bytes)
                .where(FileVariant.file_id.in_(file_ids)).order_by(FileVariant.file_id, FileVariant.kind)):
            checkpoint()
            total_rows += 1
            if total_rows > cfg.export_max_rows:
                raise _large()
            variant_values[(str(file_id), kind)] = byte_count
        assets = []
        for file in file_rows:
            sha, byte_count = hashes[str(file.id)]
            available = file.status in {"ready", "processing"} and sha is not None and byte_count is not None
            assets.append(s.AssetState(file_id=file.id, kind="original", mime=file.mime,
                bytes=byte_count, sha256=sha, available=available))
            if file.mime != "application/pdf":
                for kind in ("reading", "thumbnail"):
                    size = variant_values.get((str(file.id), kind))
                    assets.append(s.AssetState(file_id=file.id, kind=kind, mime="image/jpeg", bytes=size,
                                               sha256=None, available=size is not None and file.status == "ready"))
        checkpoint()
        asset_bytes = sum(asset.bytes or 0 for asset in assets if asset.available)
        result = s.SnapshotOut(snapshot_id=uuid4(), content_version="", owner_id=owner_id, sync_epoch=sync_state.epoch,
            created_at=utcnow(), scope=data.scope, folders=folders, notebooks=notebooks, tags=tags,
            notes=note_values, pages=pages, pdf_imports=pdfs, files=file_rows, assets=assets,
            total_rows=total_rows, metadata_bytes=0, asset_bytes=asset_bytes)
        stable = result.model_dump_json(exclude={"snapshot_id", "content_version", "created_at", "metadata_bytes"})
        result.content_version = hashlib.sha256(stable.encode()).hexdigest()
        for _ in range(4):
            size = len(result.model_dump_json().encode())
            if size == result.metadata_bytes:
                break
            result.metadata_bytes = size
        if result.metadata_bytes > cfg.export_max_metadata_bytes or result.metadata_bytes + asset_bytes > cfg.export_max_bytes:
            raise _large()
        checkpoint()
    return result
