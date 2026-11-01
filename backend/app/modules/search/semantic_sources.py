"""Local snapshots reuse T17's complete OCR validity and ownership predicates."""
import hashlib
import json
from collections import Counter
from sqlalchemy import select, and_
from app.modules.notes.models import Note, NoteTag, Tag, Folder, Notebook
from app.modules.search import keyword_service as keyword
from app.modules.search import service as ocr
from app.modules.search.models import OcrDocument
from app.modules.ai.models import AiRun
from app.shared.errors import AppError

CHUNKER = "unicode-800-overlap40-v1"
MAX_CHUNKS = 500


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_filters(db, owner, filters):
    for key, model in (("folder_id", Folder), ("notebook_id", Notebook), ("tag_id", Tag)):
        if filters.get(key) and not db.scalar(select(model.id).where(model.id == filters[key], model.owner_id == owner)):
            raise AppError(404, "search_filter_not_found", "筛选条件不存在或无权访问")


def sources(db, owner, filters=None, relevant_keys=None):
    filters = filters or {}
    result = {}
    characters = 0
    note_ids = {key.split(":")[1] for key in relevant_keys if key.startswith("note:")} if relevant_keys is not None else None
    ocr_ids = {key.split(":")[1] for key in relevant_keys if key.startswith("ocr:")} if relevant_keys is not None else None
    tag_ids = {key.split(":")[3] for key in relevant_keys if key.startswith("note:") and ":tag:" in key} if relevant_keys is not None else None

    def add(row, identity, extra=None):
        nonlocal characters
        if not row["text"] or relevant_keys is not None and identity not in relevant_keys:
            return
        characters += len(row["text"])
        if relevant_keys is None and (len(result) >= MAX_CHUNKS or characters > MAX_CHUNKS * 800):
            raise AppError(413, "semantic_chunk_limit", "资料超过500个文本块，请减少资料后再建索引")
        # JSON-normalized metadata is durable; no Python-only UUID/datetime objects.
        record = json.loads(json.dumps(dict(row), default=str))
        record["source_key"] = identity
        record["version"] = CHUNKER
        if extra:
            record.update(extra)
        record["source_hash"] = digest(record)
        result[identity] = record

    statement = select(Note).where(*keyword._filters(owner, filters)).order_by(Note.id)
    if note_ids is not None:
        statement = statement.where(Note.id.in_(note_ids))
    else:
        statement = statement.limit(MAX_CHUNKS + 1)
    notes = []
    for note in db.scalars(statement.execution_options(yield_per=10)):
        notes.append(note)
        if relevant_keys is None and len(notes) > MAX_CHUNKS:
            raise AppError(413, "semantic_chunk_limit", "资料超过500个文本块，请减少资料后再建索引")
        common = dict(id=note.id, kind="note", note_id=note.id, title=note.title, note_kind=note.kind,
                      source_revision=note.revision, ocr_revision=None, page_id=None, page_number=None,
                      file_id=None, region=None, reviewed=None, updated_at=note.updated_at)
        for field, text in (("title", note.title), ("body", note.plain_text)):
            add({**common, "matched_field": field, "text": text}, f"note:{note.id}:{field}")
    tag_statement = (select(NoteTag.note_id, Tag).join(Tag, and_(Tag.id == NoteTag.tag_id, Tag.owner_id == owner))
                      .where(NoteTag.owner_id == owner, NoteTag.note_id.in_([n.id for n in notes]))
                      .order_by(NoteTag.note_id, Tag.id))
    if tag_ids is not None:
        tag_statement = tag_statement.where(Tag.id.in_(tag_ids))
    tags = db.execute(tag_statement.limit(MAX_CHUNKS + 1)) if notes else []
    by_id = {n.id: n for n in notes}
    for note_id, tag in tags:
        note = by_id[note_id]
        add(dict(id=note.id, kind="note", note_id=note.id, title=note.title, note_kind=note.kind,
                 matched_field="tag", text=tag.name, source_revision=note.revision, ocr_revision=None,
                 page_id=None, page_number=None, file_id=None, region=None, reviewed=None,
                 updated_at=max(note.updated_at, tag.updated_at)), f"note:{note.id}:tag:{tag.id}",
            {"tag_id": str(tag.id), "tag_revision": tag.revision})
    statement = keyword._ocr(owner, "", filters)
    if ocr_ids is not None:
        rows = statement.subquery()
        statement = select(rows).where(rows.c.id.in_(ocr_ids))
    else:
        statement = statement.limit(MAX_CHUNKS + 1)
    for number, row in enumerate(db.execute(statement).mappings()):
        if number >= MAX_CHUNKS:
            raise AppError(413, "semantic_chunk_limit", "资料超过500个文本块，请减少资料后再建索引")
        document = db.get(OcrDocument, row["id"])
        try:
            ocr.assert_source(db, document, db.get(AiRun, document.run_id))
        except AppError:
            continue
        add(row, f"ocr:{row['id']}")
    return result


def manifest(db, owner, config):
    found = sources(db, owner)
    chunks = []
    for key, row in sorted(found.items()):
        text = row["text"]
        start = 0
        while start < len(text):
            end = min(len(text), start + 800)
            # Chunk text itself is the durable snapshot. Do not duplicate a complete
            # long document in every chunk's metadata.
            metadata = {name: value for name, value in row.items() if name != "text"}
            chunks.append(dict(source_key=key, source_hash=row["source_hash"], source=metadata,
                               start=start, end=end, text=text[start:end]))
            if len(chunks) > MAX_CHUNKS:
                raise AppError(413, "semantic_chunk_limit", "资料超过500个文本块，请减少资料后再建索引")
            if end == len(text):
                break
            start = end - 40
    if not chunks:
        raise AppError(409, "semantic_no_sources", "没有可以建立索引的有效文本")
    fingerprint = digest({"config": config, "sources": [(k, v["source_hash"]) for k, v in sorted(found.items())]})
    preview = dict(manifest_hash=fingerprint, config=config, source_count=len(found), chunk_count=len(chunks),
                   character_count=sum(len(c["text"]) for c in chunks), batch_count=(len(chunks) + 9) // 10,
                   max_chunks=MAX_CHUNKS, sources=dict(Counter(v["matched_field"] for v in found.values())))
    return preview, chunks


def current_chunks(db, owner, chunks, filters=None):
    current = sources(db, owner, filters, {chunk.source_key for chunk in chunks})
    return {chunk.id: current[chunk.source_key] for chunk in chunks
            if chunk.source_key in current and current[chunk.source_key]["source_hash"] == chunk.source_hash
            and chunk.text == current[chunk.source_key]["text"][chunk.start:chunk.end]}
