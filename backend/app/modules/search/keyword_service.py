"""Read-only literal keyword matching over owned current note and OCR content."""
from datetime import datetime, timedelta
import hashlib
import json
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, Integer, Numeric, String, Uuid, and_, case, cast, func, literal, null, or_, select, tuple_, union_all
from sqlalchemy.dialects.postgresql import JSONB

from app.modules.ai.models import AiRun
from app.modules.files.models import File, FileVariant, NotePage, PdfImport
from app.modules.notes.models import Folder, Note, NoteTag, Notebook, Tag
from app.modules.notes import cursors
from app.modules.search.models import OcrDocument
from app.modules.search import keyword_schemas as s, service as ocr
from app.shared.db import utcnow
from app.shared.errors import AppError

UPPER, LOWER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
FOLD = str.maketrans(UPPER, LOWER)


def contains(column, keyword):
    # translate preserves code-point lengths, unlike unrestricted Unicode case folding.
    return func.strpos(func.translate(column, UPPER, LOWER), keyword.translate(FOLD)) > 0


def _filters(owner, filters):
    conditions = [Note.owner_id == owner, Note.deleted_at.is_(None)]
    if filters.get("kind"):
        conditions.append(Note.kind == filters["kind"])
    if filters.get("notebook_id"):
        conditions.append(Note.notebook_id == filters["notebook_id"])
    if filters.get("folder_id"):
        conditions.append(Note.notebook_id.in_(select(Notebook.id).where(
            Notebook.owner_id == owner, Notebook.folder_id == filters["folder_id"])))
    if filters.get("tag_id"):
        conditions.append(Note.id.in_(select(NoteTag.note_id).where(
            NoteTag.owner_id == owner, NoteTag.tag_id == filters["tag_id"])))
    return conditions


def _json_int(value):
    # Guard the cast inside CASE, not a separate WHERE term the optimizer can reorder.
    text = value.astext
    return case((and_(func.jsonb_typeof(value) == "number", text.op("~")(r"^[1-9][0-9]{0,9}$")),
                 cast(text, BigInteger)), else_=None)


def _region(region):
    absent = or_(func.jsonb_typeof(region).is_(None), func.jsonb_typeof(region) == "null")
    coords = [case((func.jsonb_typeof(region[name]) == "number", cast(region[name].astext, Numeric)),
                   else_=None) for name in ("x", "y", "width", "height")]
    x, y, w, h = coords
    valid = and_(func.jsonb_typeof(region) == "object",
                 region == func.jsonb_build_object("x", region["x"], "y", region["y"],
                                                   "width", region["width"], "height", region["height"]),
                 x >= 0, x < 1, y >= 0, y < 1, w > 0, w <= 1, h > 0, h <= 1,
                 x + w <= 1, y + h <= 1)
    return absent, valid


def _notes(owner, keyword, filters):
    tags = (select(NoteTag.note_id.label("note_id"),
                   func.min(Tag.name).filter(contains(Tag.name, keyword)).label("matching_tag"),
                   func.max(Tag.updated_at).label("tag_at"))
            .join(Tag, and_(Tag.id == NoteTag.tag_id, Tag.owner_id == owner))
            .where(NoteTag.owner_id == owner).group_by(NoteTag.note_id).subquery())
    title = contains(Note.title, keyword)
    tagged = tags.c.matching_tag.is_not(None)
    field = case((title, "title"), (tagged, "tag"), else_="body")
    text = case((title, Note.title), (tagged, tags.c.matching_tag), else_=Note.plain_text)
    return (select(Note.id.label("id"), literal("note").label("kind"), Note.id.label("note_id"),
                   Note.title.label("title"), Note.kind.label("note_kind"), field.label("matched_field"),
                   text.label("text"), Note.revision.label("source_revision"),
                   cast(null(), Integer).label("ocr_revision"), cast(null(), Uuid).label("page_id"),
                   cast(null(), Integer).label("page_number"), cast(null(), Uuid).label("file_id"),
                   cast(null(), JSONB).label("region"), cast(null(), Boolean).label("reviewed"),
                   func.greatest(Note.updated_at, func.coalesce(tags.c.tag_at, Note.updated_at)).label("updated_at"))
            .outerjoin(tags, tags.c.note_id == Note.id)
            .where(*_filters(owner, filters), or_(title, tagged, contains(Note.plain_text, keyword))))


def _ocr(owner, keyword, filters):
    source, page = AiRun.source, AiRun.source["pages"][0]
    region = page["region"]
    absent, valid_region = _region(region)
    number, revision, page_revision = _json_int(page["number"]), _json_int(source["revision"]), _json_int(page["page_revision"])
    count = case((func.jsonb_typeof(source["pages"]) == "array", func.jsonb_array_length(source["pages"])), else_=0)
    image = and_(NotePage.pdf_page_number.is_(None), source["kind"].astext == "image",
                 or_(and_(source["mode"].astext == "page", absent),
                     and_(source["mode"].astext == "region", valid_region)))
    pdf = and_(NotePage.pdf_page_number.is_not(None),
               or_(func.jsonb_typeof(source["kind"]).is_(None), source["kind"].astext == "pdf"),
               PdfImport.status == "ready", PdfImport.page_count == PdfImport.completed_pages,
               PdfImport.page_count.between(1, 200), number.between(1, 200), number == NotePage.pdf_page_number,
               or_(and_(source["mode"].astext == "pages", absent),
                   and_(source["mode"].astext == "all", absent, PdfImport.page_count == 1),
                   and_(source["mode"].astext == "region", valid_region)))
    ranked = (select(OcrDocument.id.label("id"), literal("ocr").label("kind"), Note.id.label("note_id"),
                     Note.title.label("title"), Note.kind.label("note_kind"), literal("ocr").label("matched_field"),
                     func.coalesce(OcrDocument.corrected_text, AiRun.answer).label("text"),
                     revision.label("source_revision"), OcrDocument.revision.label("ocr_revision"),
                     OcrDocument.page_id.label("page_id"), number.label("page_number"),
                     OcrDocument.file_id.label("file_id"), region.label("region"),
                     OcrDocument.reviewed.label("reviewed"),
                     func.greatest(OcrDocument.updated_at, AiRun.finished_at, Note.updated_at).label("updated_at"),
                     # JSONB equality treats 0 and 0.0 as the same coordinate.
                     func.row_number().over(partition_by=[OcrDocument.page_id, func.coalesce(region, cast(literal("null"), JSONB))],
                         order_by=[OcrDocument.created_at.desc(), OcrDocument.id.desc()]).label("winner"))
              .select_from(OcrDocument)
              .join(Note, and_(Note.id == OcrDocument.note_id, Note.owner_id == owner))
              .join(AiRun, and_(AiRun.id == OcrDocument.run_id, AiRun.owner_id == owner))
              .join(NotePage, and_(NotePage.id == OcrDocument.page_id, NotePage.owner_id == owner,
                                  NotePage.note_id == Note.id, NotePage.file_id == OcrDocument.file_id,
                                  NotePage.deleted_at.is_(None)))
              .join(File, and_(File.id == OcrDocument.file_id, File.owner_id == owner))
              .join(FileVariant, and_(FileVariant.file_id == File.id, FileVariant.kind == "reading"))
              .outerjoin(PdfImport, and_(PdfImport.note_id == Note.id, PdfImport.owner_id == owner))
              .where(*_filters(owner, filters), OcrDocument.owner_id == owner, Note.kind == "image",
                     AiRun.status == "succeeded", File.status == "ready",
                     File.sha256.is_not_distinct_from(OcrDocument.source_sha256),
                     File.width.is_not_distinct_from(OcrDocument.source_width),
                     File.height.is_not_distinct_from(OcrDocument.source_height),
                     func.jsonb_typeof(source) == "object", func.jsonb_typeof(source["title"]) == "string",
                     count == 1, revision.between(1, 2147483647), page_revision.between(1, 2147483647),
                     number.between(1, 2147483647), source["note_id"].astext == cast(Note.id, String),
                     page["page_id"].astext == cast(NotePage.id, String),
                     page["file_id"].astext == cast(File.id, String),
                     page["object_key"].astext == FileVariant.object_key,
                     func.jsonb_typeof(page["bytes"]) == "number",
                     page["bytes"].astext == cast(FileVariant.bytes, String), FileVariant.bytes > 0,
                     or_(image, pdf)).subquery())
    # Choose the newest successful scope BEFORE looking for the keyword.
    return select(*(column for column in ranked.c if column.name != "winner")).where(
        ranked.c.winner == 1, contains(ranked.c.text, keyword))


def candidates(owner, keyword, filters):
    return union_all(_notes(owner, keyword, filters), _ocr(owner, keyword, filters)).subquery()


def _position(token, scope):
    value = cursors.decode_cursor(token, {"v", "scope", "cutoff", "at", "kind", "id"}, scope)
    try:
        cutoff, at, identity = datetime.fromisoformat(value["cutoff"]), datetime.fromisoformat(value["at"]), UUID(value["id"])
        if (value["kind"] not in {"note", "ocr"} or str(identity) != value["id"]
                or cutoff.utcoffset() != timedelta(0) or at.utcoffset() != timedelta(0)
                or cutoff.isoformat() != value["cutoff"] or at.isoformat() != value["at"]
                or at > cutoff or cutoff > utcnow()):
            raise ValueError("Invalid search position")
        return cutoff, (at, value["kind"], identity)
    except (ValueError, TypeError, AttributeError):
        raise cursors.invalid_cursor() from None


def _match(text, keyword):
    start = text.translate(FOLD).find(keyword.translate(FOLD))
    if start < 0:
        raise AppError(409, "search_result_changed", "搜索内容已变化，请重新搜索")
    return start, start + len(keyword)


def _fingerprint(row):
    # A tag can change without revising its note. This is a content comparison,
    # never an authorization token: owner/source checks remain mandatory.
    fields = {key: row[key] for key in ("id", "kind", "note_id", "title", "matched_field", "text",
              "source_revision", "ocr_revision", "page_id", "page_number", "file_id", "region", "reviewed")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _hit(row, keyword):
    start, end = _match(row["text"], keyword)
    left, right = max(0, start - 40), min(len(row["text"]), max(start + len(keyword) + 40, 180 if start < 40 else start + 140))
    # Keep the preview bounded even for a match near the end of a large document.
    right = min(right, left + 180)
    prefix = "…" if left else ""
    snippet = prefix + row["text"][left:right] + ("…" if right < len(row["text"]) else "")
    public = {key: value for key, value in row.items() if key not in {"text", "file_id"}}
    return s.SearchHitOut(**public, snippet=snippet, match_hash=_fingerprint(row), match_start=len(prefix) + start - left,
                          match_end=len(prefix) + end - left)


def search(db, owner, keyword, limit, cursor, **filters):
    for key, model in (("folder_id", Folder), ("notebook_id", Notebook), ("tag_id", Tag)):
        if filters.get(key) and not db.scalar(select(model.id).where(model.id == filters[key], model.owner_id == owner)):
            raise AppError(404, "search_filter_not_found", "筛选条件不存在或无权访问")
    scope = cursors.scope_key(owner, keyword, sorted((key, str(value) if value else None) for key, value in filters.items()), "keyword-v1")
    cutoff, anchor = _position(cursor, scope) if cursor else (utcnow(), None)
    rows = candidates(owner, keyword, filters)
    query = select(rows).where(rows.c.updated_at <= cutoff)
    if anchor:
        query = query.where(tuple_(rows.c.updated_at, rows.c.kind, rows.c.id) < anchor)
    found = list(db.execute(query.order_by(rows.c.updated_at.desc(), rows.c.kind.desc(), rows.c.id.desc()).limit(limit + 1)).mappings())
    page, next_cursor = found[:limit], None
    if len(found) > limit:
        last = page[-1]
        next_cursor = cursors.encode_cursor({"v": 1, "scope": scope, "cutoff": cutoff.isoformat(),
                    "at": last["updated_at"].isoformat(), "kind": last["kind"], "id": str(last["id"])})
    return s.SearchPageOut(items=[_hit(row, keyword) for row in page], next_cursor=next_cursor, limit=limit)


def resolve(db, owner, kind, identity, keyword, source_revision, ocr_revision, match_hash):
    if kind == "note":
        document = None
        note = db.scalar(select(Note).where(Note.id == identity, Note.owner_id == owner, Note.deleted_at.is_(None)))
    else:
        document = db.scalar(select(OcrDocument).where(OcrDocument.id == identity, OcrDocument.owner_id == owner))
        note = db.scalar(select(Note).where(Note.id == document.note_id, Note.owner_id == owner,
                                            Note.deleted_at.is_(None))) if document else None
    if not note:
        raise AppError(404, "search_source_not_found", "搜索来源不存在或无权访问")
    rows = candidates(owner, keyword, {})
    row = db.execute(select(rows).where(rows.c.kind == kind, rows.c.id == identity)).mappings().first()
    if (not row or row["source_revision"] != source_revision or _fingerprint(row) != match_hash
            or kind == "ocr" and (ocr_revision is None or row["ocr_revision"] != ocr_revision)):
        raise AppError(409, "search_result_changed", "搜索内容或识别来源已变化，请重新搜索")
    if document:
        ocr.assert_source(db, document, db.get(AiRun, document.run_id))
    start, end = _match(row["text"], keyword)
    return s.SearchSourceOut(**{key: value for key, value in row.items() if key not in {"note_kind", "updated_at"}},
                             match_start=start, match_end=end, match_hash=_fingerprint(row))
