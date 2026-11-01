"""Verify an account export without extracting it or contacting any service.

Historical snapshots may refer to organizations that have since been removed.
Those historical references are preserved, not validated as current foreign keys;
a future importer must remap them according to its documented restore policy.
Checksums detect damage, not malicious replacement of both data and its manifest.
"""

import argparse
from datetime import datetime
import hashlib
from itertools import chain
import json
import math
from pathlib import Path
import re
import stat
import sys
from uuid import UUID
import zipfile


FORMAT = "ai-notes-export"
VERSION = 3
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_METADATA_BYTES = 32 * 1024 * 1024
MAX_ROWS = 50_000
MAX_ENTRIES = 50_002
CHUNK_BYTES = 256 * 1024
V1_TABLES = (
    "folders", "notebooks", "tags", "notes", "note_tags", "note_versions",
    "files", "note_pages", "file_variants", "pdf_imports", "ai_conversations",
    "ai_runs", "ai_steps", "ai_saved_notes",
)
V2_TABLES = V1_TABLES + ("ocr_documents",)
LEARNING_TABLES = ("learning_decks", "card_generations", "learning_cards", "learning_quizzes", "quiz_questions",
    "quiz_attempts", "quiz_answers", "quiz_operations", "quiz_answer_reviews", "quiz_generations", "quiz_gradings",
    "review_items", "review_plans", "review_logs", "user_learning_settings", "daily_review_digests", "learning_events")
LEARNING_COLUMNS = {name:frozenset(('id created_at owner_id '+extra).split()) for name,extra in {
    'learning_decks':'name status revision updated_at',
    'card_generations':'deck_id run_id request_key request_hash kind count source status error_code finished_at',
    'learning_cards':'deck_id kind front back payload source status revision updated_at generation_id generation_ordinal',
    'learning_quizzes':'title status revision question_count updated_at',
    'quiz_questions':'quiz_id position kind prompt options correct_answer explanation max_points source',
    'quiz_attempts':'quiz_id request_key quiz_revision title snapshot status revision total_points max_points submitted_at finished_at',
    'quiz_answers':'attempt_id question_id answer revision status points feedback provenance',
    'quiz_operations':'attempt_id request_key request_hash result',
    'quiz_answer_reviews':'answer_id old_grade new_grade reason request_key',
    'quiz_generations':'quiz_id run_id request_key request_hash title kinds count source status error_code finished_at',
    'quiz_gradings':'answer_id answer_revision attempt_revision run_id request_key request_hash input status result error_code finished_at',
    'review_items':'kind note_id card_id quiz_id',
    'review_plans':'item_id strategy status next_due_at algorithm_version scheduler_state last_fsrs_state revision updated_at',
    'review_logs':'item_id plan_id request_key request_hash action rating scheduled_for reviewed_at old_snapshot new_snapshot reason result_plan',
    'user_learning_settings':'timezone review_time reminder_enabled revision updated_at',
    'daily_review_digests':'kind local_date timezone counts',
    'learning_events':'event_id kind note_id card_id quiz_id started_at ended_at request_hash',
}.items()}
TABLES = V2_TABLES + LEARNING_TABLES
TABLES_BY_VERSION = {1: V1_TABLES, 2: V2_TABLES, 3: TABLES}
OWNED_TABLES = set(TABLES) - {"file_variants", "ai_steps"}
UUID_TEXT = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
ASSET_PATH = re.compile(rf"objects/({UUID_TEXT})/(?:original|variants/{UUID_TEXT})")
TEXT_SOURCE_FIELDS = {"kind", "note_id", "revision", "title", "start", "end", "quote"}


class ValidationError(ValueError):
    """A short, non-sensitive validation code suitable for CLI output."""


def _require(condition, code):
    if not condition:
        raise ValidationError(code)


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _source_shape(source):
    """Validate frozen source scope without importing server dependencies."""
    if source.get("kind") == "text":
        _require(set(source) == TEXT_SOURCE_FIELDS, "invalid_source")
        _uuid(source.get("note_id"))
        _require(_integer(source.get("revision")) and source["revision"] >= 1
                 and isinstance(source.get("title"), str) and 0 < len(source["title"]) <= 200
                 and bool(source["title"].strip()), "invalid_source")
        _require(_integer(source.get("start")) and source["start"] >= 0
                 and _integer(source.get("end")) and source["end"] > source["start"], "invalid_source")
        quote = source.get("quote")
        _require(isinstance(quote, str) and 0 < len(quote) <= 8000 and bool(quote.strip())
                 and source["end"] - source["start"] == len(quote), "invalid_source")
        _valid_text(source["title"])
        _valid_text(quote)
        return
    _require(source.get("kind") in {"pdf", "image"}, "invalid_source")
    image = source["kind"] == "image"
    _require(source.get("mode") in ({"page", "region"} if image else {"pages", "all", "region"}), "invalid_source")
    pages = source.get("pages")
    _require(isinstance(pages, list) and 0 < len(pages) <= 200, "invalid_source")
    if image or source["mode"] == "region":
        _require(len(pages) == 1, "invalid_source")
    ids, numbers = set(), set()
    for page in pages:
        _require(isinstance(page, dict), "invalid_source")
        identity, number = _uuid(page.get("page_id")), page.get("number")
        _require(_integer(number) and number >= 1 and (image or number <= 200)
                 and identity not in ids and number not in numbers, "invalid_source")
        ids.add(identity); numbers.add(number)
        region = page.get("region")
        _require((source["mode"] == "region") == (region is not None), "invalid_source")
        if region is not None:
            _require(isinstance(region, dict) and set(region) == {"x", "y", "width", "height"}, "invalid_source")
            _require(all(type(value) in {int, float} and 0 <= value <= 1
                         and math.isfinite(value) for value in region.values()), "invalid_source")
            _require(0 <= region["x"] < 1 and 0 <= region["y"] < 1
                     and 0 < region["width"] <= 1 and 0 < region["height"] <= 1
                     and region["x"] + region["width"] <= 1
                     and region["y"] + region["height"] <= 1, "invalid_source")


def _valid_text(value):
    _require(isinstance(value, str) and "\x00" not in value, "invalid_source")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValidationError("invalid_source") from None


def _canonical_text(body):
    """Mirror text_context.canonical_text with bounded, dependency-free input."""
    _require(isinstance(body, dict), "invalid_text_snapshot")
    _require(not body or body.get("type") == "doc" and isinstance(body.get("content"), list), "invalid_text_snapshot")
    stack, nodes = [(body, 0)], 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        _require(depth <= 64 and nodes <= 50_000, "invalid_text_snapshot")
        if isinstance(value, dict):
            for key in value:
                _valid_text(key)
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
        elif isinstance(value, str):
            _valid_text(value)
    _require(len(json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
             <= 1_048_576, "invalid_text_snapshot")
    stack = [body]
    while stack:
        node = stack.pop()
        _require(isinstance(node, dict), "invalid_text_snapshot")
        kind = node.get("type")
        _require(kind is None or isinstance(kind, str), "invalid_text_snapshot")
        children = node.get("content", [])
        _require(isinstance(children, list), "invalid_text_snapshot")
        if kind == "text":
            _valid_text(node.get("text"))
        if kind in {"inlineMath", "blockMath"}:
            attrs = node.get("attrs", {})
            _require(isinstance(attrs, dict), "invalid_text_snapshot")
            _valid_text(attrs.get("latex", ""))
        stack.extend(children)

    def derive(node):
        kind = node.get("type")
        children = node.get("content", [])
        if kind == "text":
            return node["text"]
        if kind in {"inlineMath", "blockMath"}:
            return node.get("attrs", {}).get("latex", "")
        if kind == "hardBreak":
            return "\n"
        separator = "\n" if kind in {"doc", "bulletList", "orderedList", "listItem", "blockquote"} else ""
        return separator.join(derive(child) for child in children)

    return derive(body)


def _text_snapshot(source, snapshot, owner):
    _require(isinstance(snapshot, dict) and snapshot.get("id") == source["note_id"]
             and snapshot.get("owner_id") == owner and snapshot.get("kind") == "text"
             and type(snapshot.get("revision")) is int and snapshot["revision"] == source["revision"]
             and snapshot.get("title") == source["title"], "source_mismatch")
    canonical = _canonical_text(snapshot.get("body_json"))
    _require(source["end"] <= len(canonical)
             and canonical[source["start"]:source["end"]] == source["quote"], "source_mismatch")


def _uuid(value):
    if not isinstance(value, str):
        raise ValidationError("invalid_identifier")
    try:
        _require(str(UUID(value)) == value, "invalid_identifier")
    except ValueError:
        raise ValidationError("invalid_identifier") from None
    return value


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValidationError("invalid_json")

    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid_constant)


def _metadata(archive, entry):
    _require(entry.file_size <= MAX_METADATA_BYTES, "metadata_too_large")
    with archive.open(entry) as stream:
        data = stream.read(MAX_METADATA_BYTES + 1)
    _require(len(data) <= MAX_METADATA_BYTES, "metadata_too_large")
    return _json(data)


def _references(collections, payloads, owner):
    payload_paths = set(payloads)
    indexed = {}
    rows = 0
    for table in collections:
        items = collections[table]
        _require(isinstance(items, list), "invalid_collection")
        rows += len(items)
        _require(rows <= MAX_ROWS, "too_many_rows")
        by_id = {}
        for row in items:
            _require(isinstance(row, dict), "invalid_record")
            if table in OWNED_TABLES:
                _require(row.get("owner_id") == owner, "owner_mismatch")
            if table != "note_tags":
                identity = _uuid(row.get("id"))
                _require(identity not in by_id, "duplicate_record")
                by_id[identity] = row
        indexed[table] = by_id

    def ref(row, field, table, nullable=False):
        value = row.get(field)
        if nullable and value is None:
            return
        _require(_uuid(value) in indexed[table], "missing_reference")

    for row in collections["folders"]:
        ref(row, "parent_id", "folders", nullable=True)
        _require(row.get("parent_id") != row["id"], "invalid_folder_parent")
    # The live model forbids cycles; reject cycles even if every ID exists.
    completed = set()
    for identity in indexed["folders"]:
        visiting = set()
        cursor = identity
        while cursor is not None and cursor not in completed:
            _require(cursor not in visiting, "invalid_folder_parent")
            visiting.add(cursor)
            cursor = indexed["folders"][cursor].get("parent_id")
        completed.update(visiting)
    for row in collections["notebooks"]:
        ref(row, "folder_id", "folders", nullable=True)
    for row in collections["notes"]:
        ref(row, "notebook_id", "notebooks", nullable=True)
    note_tags = set()
    for row in collections["note_tags"]:
        ref(row, "note_id", "notes")
        ref(row, "tag_id", "tags")
        identity = (row["note_id"], row["tag_id"])
        _require(identity not in note_tags, "duplicate_record")
        note_tags.add(identity)
    versions = {}
    for row in collections["note_versions"]:
        ref(row, "note_id", "notes")
        _require(_integer(row.get("revision")) and row["revision"] >= 1, "invalid_version")
        identity = (row["note_id"], row["revision"])
        _require(identity not in versions, "duplicate_record")
        versions[identity] = row

    used_assets = set()
    for row in collections["files"]:
        path = row.get("original_path")
        if path is None:
            expired = row.get("status") == "expired" and row.get("original_unavailable_reason") == "upload_expired"
            rejected_image = (row.get("status") == "rejected" and row.get("mime") != "application/pdf" and
                              row.get("original_unavailable_reason") == "image_rejected")
            _require(expired or rejected_image, "missing_asset")
        else:
            _require(path == f"objects/{row['id']}/original" and path in payload_paths,
                     "missing_asset")
            _require(_integer(row.get("bytes")) and row["bytes"] > 0 and
                     row["bytes"] == payloads[path]["bytes"] and
                     (row.get("sha256") is None or row["sha256"] == payloads[path]["sha256"]),
                     "asset_metadata_mismatch")
            used_assets.add(path)
    variants = {}
    for row in collections["file_variants"]:
        ref(row, "file_id", "files")
        path = row.get("path")
        _require(path == f"objects/{row['file_id']}/variants/{row['id']}" and
                 path in payload_paths, "missing_asset")
        _require(row.get("kind") in {"reading", "thumbnail"} and
                 _integer(row.get("bytes")) and row["bytes"] > 0 and
                 row["bytes"] == payloads[path]["bytes"], "asset_metadata_mismatch")
        identity = (row["file_id"], row.get("kind"))
        _require(identity not in variants, "duplicate_record")
        variants[identity] = row
        used_assets.add(path)
    positions = set()
    for row in collections["note_pages"]:
        ref(row, "note_id", "notes")
        ref(row, "file_id", "files")
        position = row.get("position")
        _require(_integer(position) and position >= 0, "invalid_page")
        identity = (row["note_id"], position)
        _require(identity not in positions, "duplicate_record")
        positions.add(identity)
    for row in collections["pdf_imports"]:
        ref(row, "note_id", "notes")
        ref(row, "source_file_id", "files")
    for row in collections["ai_runs"]:
        ref(row, "conversation_id", "ai_conversations")
    for row in collections["ai_steps"]:
        ref(row, "run_id", "ai_runs")
    for row in collections["ai_saved_notes"]:
        ref(row, "note_id", "notes")
        ref(row, "run_id", "ai_runs")

    ocr_runs = {}
    for row in collections.get("ocr_documents", []):
        ref(row, "note_id", "notes")
        ref(row, "file_id", "files")
        ref(row, "run_id", "ai_runs")
        _uuid(row.get("page_id"))  # Historical page identity intentionally has no FK.
        _require(set(row) == {"id", "created_at", "owner_id", "note_id", "page_id", "file_id", "run_id",
                              "source_sha256", "source_width", "source_height", "corrected_text", "reviewed",
                              "revision", "updated_at"}, "invalid_ocr")
        _require(row["run_id"] not in ocr_runs, "duplicate_record")
        ocr_runs[row["run_id"]] = row
        _require(_integer(row["revision"]) and 1 <= row["revision"] < 2**31 and type(row["reviewed"]) is bool,
                 "invalid_ocr")
        for field in ("created_at", "updated_at"):
            _require(isinstance(row[field], str) and datetime.fromisoformat(row[field]).utcoffset() is not None,
                     "invalid_timestamp")
        _require(row["source_sha256"] is None or isinstance(row["source_sha256"], str)
                 and re.fullmatch(r"[0-9a-f]{64}", row["source_sha256"]), "invalid_ocr")
        for field in ("source_width", "source_height"):
            _require(row[field] is None or _integer(row[field]) and 0 < row[field] < 2**31, "invalid_ocr")
        _require((row["source_width"] is None) == (row["source_height"] is None), "invalid_ocr")
        corrected = row["corrected_text"]
        if corrected is not None:
            _valid_text(corrected)
            _require(len(corrected) <= 64_000, "invalid_ocr")
        _require(not row["reviewed"] or corrected is not None, "invalid_ocr")
        run = indexed["ai_runs"][row["run_id"]]
        _require(run.get("status") in {"succeeded", "failed", "interrupted", "cancelled", "truncated"},
                 "unsafe_processing_state")
        _require(not row["reviewed"] or run.get("status") == "succeeded", "invalid_ocr")
        source = run.get("source")
        _require(isinstance(source, dict) and source.get("kind") in {"image", "pdf"}
                 and source.get("note_id") == row["note_id"]
                 and isinstance(source.get("pages"), list) and len(source["pages"]) == 1
                 and isinstance(source["pages"][0], dict)
                 and source["pages"][0].get("page_id") == row["page_id"]
                 and source["pages"][0].get("file_id") == row["file_id"], "invalid_ocr")
        _require(set(source) == {"kind", "note_id", "title", "revision", "mode", "pages"}
                 and _integer(source["revision"]) and 1 <= source["revision"] < 2**31
                 and isinstance(source["title"], str) and 0 < len(source["title"]) <= 200 and source["title"].strip(),
                 "invalid_ocr")
        _valid_text(source["title"])
        frozen = source["pages"][0]
        _require({"page_id", "number", "file_id", "reading_path", "page_revision"} <= set(frozen)
                 <= {"page_id", "number", "file_id", "reading_path", "page_revision", "region"}
                 and _integer(frozen["page_revision"]) and 1 <= frozen["page_revision"] < 2**31, "invalid_ocr")
        _require(indexed["notes"][row["note_id"]].get("kind") == "image", "invalid_ocr")
        actual = indexed["note_pages"].get(row["page_id"])
        _require(actual is None or actual["note_id"] == row["note_id"], "source_mismatch")

    learning_runs = {row['run_id'] for table in ('card_generations','quiz_generations') for row in collections.get(table,[])}
    for row, historical in chain(
        ((row, row["id"] in ocr_runs or row['id'] in learning_runs) for row in collections["ai_runs"]),
        ((row, False) for row in collections["ai_saved_notes"]),
    ):
        source = row.get("source")
        if source is None:
            continue
        _require(isinstance(source, dict), "invalid_source")
        _source_shape(source)
        ref(source, "note_id", "notes")
        if source["kind"] == "text":
            _require(indexed["notes"][source["note_id"]].get("kind") == "text", "source_mismatch")
            version = versions.get((source["note_id"], source["revision"]))
            _require(version is not None, "missing_reference")
            _text_snapshot(source, version.get("snapshot"), owner)
            if "run_id" in row:
                _require(row.get("cited_page_numbers") == []
                         and indexed["ai_runs"][row["run_id"]].get("source") == source, "invalid_source")
            continue
        pages = source.get("pages")
        _require(isinstance(pages, list) and 0 < len(pages) <= MAX_ROWS, "invalid_source")
        for page in pages:
            _require(isinstance(page, dict), "invalid_source")
            if not historical:
                ref(page, "page_id", "note_pages")
            ref(page, "file_id", "files")
            if not historical:
                actual = indexed["note_pages"][page["page_id"]]
                _require(actual["note_id"] == source["note_id"] and
                         actual["file_id"] == page["file_id"], "source_mismatch")
            reading = variants.get((page["file_id"], "reading"))
            _require(reading is not None and page.get("reading_path") == reading["path"],
                     "source_mismatch")
    if "learning_cards" in collections:
        _learning_references(collections, indexed, payloads, variants, versions, owner)
    _require(used_assets == payload_paths - {"data.json"}, "unreferenced_asset")


def _learning_references(collections, indexed, payloads, variants, versions, owner):
    """Validate v3 learning identity and asset coverage without server packages."""
    def ref(row, key, table, nullable=False):
        value = row.get(key)
        if value is None and nullable:
            return
        _require(_uuid(value) in indexed[table], "missing_reference")

    def stamp(value):
        _require(isinstance(value, str), "invalid_learning_time")
        parsed = datetime.fromisoformat(value)
        _require(parsed.utcoffset() is not None, "invalid_learning_time")
        return parsed

    def source(value):
        if value is None:
            return
        _require(isinstance(value, dict), "invalid_source")
        safe = {k:v for k,v in value.items() if k != "_card_identity"}
        _source_shape(safe)
        ref(safe, "note_id", "notes")
        if safe['kind'] == 'text':
            _require(set(value) == TEXT_SOURCE_FIELDS, "invalid_source")
            version = versions.get((safe['note_id'],safe['revision']))
            _require(version is not None, "missing_reference")
            _text_snapshot(safe, version['snapshot'], owner)
            return
        _require(set(value) == {'kind','note_id','title','revision','mode','pages','_card_identity'}
                 and len(safe['pages']) == 1, 'invalid_source')
        identity = value['_card_identity']
        _require(isinstance(identity,dict) and set(identity) == {'sha256','width','height','reading_sha256'}, 'invalid_source')
        for key in ('sha256','reading_sha256'):
            _require(isinstance(identity[key],str) and re.fullmatch(r'[0-9a-f]{64}',identity[key]), 'invalid_source')
        for key in ('width','height'):
            _require(_integer(identity[key]) and identity[key]>0, 'invalid_source')
        for page in safe['pages']:
            _require({'page_id','number','file_id','reading_path'} <= set(page)
                     <= {'page_id','number','file_id','reading_path','page_revision','region'}, 'invalid_source')
            if safe['kind']=='image' or 'page_revision' in page:
                _require(_integer(page.get('page_revision')) and page['page_revision']>0, 'invalid_source')
            ref(page,'file_id','files')
            file = indexed['files'][page['file_id']]
            actual = indexed['note_pages'].get(page['page_id'])
            _require(actual is None or actual['note_id'] == safe['note_id'], 'source_mismatch')
            reading = variants.get((page['file_id'],'reading'))
            _require(reading is not None and reading['path'] == page['reading_path']
                     and (file['sha256'],file['width'],file['height']) == (identity['sha256'],identity['width'],identity['height'])
                     and payloads[reading['path']]['sha256'] == identity['reading_sha256'], 'source_mismatch')

    links = {
        'card_generations': [('deck_id','learning_decks',False),('run_id','ai_runs',False)],
        'learning_cards': [('deck_id','learning_decks',False),('generation_id','card_generations',True)],
        'quiz_questions': [('quiz_id','learning_quizzes',False)],
        'quiz_attempts': [('quiz_id','learning_quizzes',False)],
        'quiz_answers': [('attempt_id','quiz_attempts',False)],
        'quiz_operations': [('attempt_id','quiz_attempts',False)],
        'quiz_answer_reviews': [('answer_id','quiz_answers',False)],
        'quiz_generations': [('quiz_id','learning_quizzes',True),('run_id','ai_runs',False)],
        'quiz_gradings': [('answer_id','quiz_answers',False),('run_id','ai_runs',False)],
        'review_plans': [('item_id','review_items',False)],
        'review_logs': [('item_id','review_items',False),('plan_id','review_plans',True)],
    }
    for table in LEARNING_TABLES:
        for row in collections[table]:
            _require(set(row)==LEARNING_COLUMNS[table], 'invalid_learning_fields')
            for key,target,nullable in links.get(table,[]):
                ref(row,key,target,nullable)
            if 'source' in row:
                source(row['source'])
            if 'request_hash' in row:
                _require(isinstance(row['request_hash'],str) and re.fullmatch(r'[0-9a-f]{64}',row['request_hash']), 'invalid_learning_receipt')
            if 'request_key' in row:
                _uuid(row['request_key'])
            if 'revision' in row:
                _require(_integer(row['revision']) and row['revision']>0, 'invalid_learning_revision')
            stamp(row['created_at'])
    for table in ('card_generations','quiz_generations','quiz_gradings'):
        for row in collections[table]:
            _require(row['status'] in {'succeeded','failed'}, 'unfinished_learning_request')
    snapshots = {}
    for attempt in collections['quiz_attempts']:
        snapshot = attempt['snapshot']
        _require(isinstance(snapshot,list) and 1 <= len(snapshot) <= 50, 'invalid_learning_snapshot')
        ids = set()
        for question in snapshot:
            identity = _uuid(question['id'])
            _require(identity not in ids, 'duplicate_record')
            ids.add(identity)
            source(question['source'])
        snapshots[attempt['id']] = ids
    for answer in collections['quiz_answers']:
        _require(answer['question_id'] in snapshots[answer['attempt_id']], 'missing_reference')
    for table in ('review_items','learning_events'):
        for row in collections[table]:
            present = [(kind,row.get(kind+'_id')) for kind in ('note','card','quiz') if row.get(kind+'_id') is not None]
            _require(len(present)==1, 'invalid_learning_resource')
            kind,_ = present[0]
            ref(row,kind+'_id',{'note':'notes','card':'learning_cards','quiz':'learning_quizzes'}[kind])
            if table == 'review_items':
                _require(row['kind']==kind, 'invalid_learning_resource')
    for row in collections['review_logs']:
        if row['plan_id'] is not None:
            _require(indexed['review_plans'][row['plan_id']]['item_id']==row['item_id'], 'source_mismatch')
    for row in collections['review_plans']:
        _require(row['strategy'] in {'manual','fsrs'} and row['status'] in {'active','paused','archived'}, 'invalid_learning_plan')
        expected = 'manual/1' if row['strategy']=='manual' else 'py-fsrs/6.3.2'
        _require(row['algorithm_version']==expected and isinstance(row['scheduler_state'],dict), 'invalid_learning_plan')
        if row['strategy']=='manual':
            _require(row['scheduler_state']=={}, 'invalid_learning_plan')
        else:
            _require(set(row['scheduler_state'])=={'card'} and isinstance(row['scheduler_state']['card'],dict), 'invalid_learning_plan')
        saved = row['last_fsrs_state']
        _require(saved is None or isinstance(saved,dict) and set(saved)=={'algorithm_version','scheduler_state'}
                 and saved['algorithm_version']=='py-fsrs/6.3.2' and isinstance(saved['scheduler_state'],dict), 'invalid_learning_plan')
    for row in collections['learning_events']:
        _uuid(row['event_id'])
        started,ended = stamp(row['started_at']),stamp(row['ended_at'])
        _require(0<(ended-started).total_seconds()<=60, 'invalid_learning_time')
        expected = {'reading':'note_id','card_practice':'card_id','quiz':'quiz_id'}
        _require(row['kind']=='review' or row['kind'] in expected and row[expected[row['kind']]] is not None, 'invalid_learning_resource')
    for row in collections['daily_review_digests']:
        from datetime import date
        _require(isinstance(row['local_date'],str) and date.fromisoformat(row['local_date']).isoformat()==row['local_date']
                 and row['kind']=='daily_review' and isinstance(row['counts'],dict)
                 and set(row['counts'])=={'note','card','quiz'}
                 and all(_integer(v) and v>=0 for v in row['counts'].values()), 'invalid_learning_digest')


def verify_archive(path):
    """Return table counts after integrity checks; never extract archive entries."""
    try:
        _require(Path(path).stat().st_size <= MAX_TOTAL_BYTES + 16 * 1024 * 1024,
                 "archive_too_large")
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            _require(2 <= len(entries) <= MAX_ENTRIES, "invalid_entry_count")
            by_path = {}
            for entry in entries:
                name = entry.filename
                _require(entry.orig_filename == name, "unsafe_entry_path")
                _require(name in {"manifest.json", "data.json"} or
                         ASSET_PATH.fullmatch(name) is not None, "unsafe_entry_path")
                _require(name not in by_path, "duplicate_entry")
                mode = entry.external_attr >> 16
                _require(not entry.is_dir() and not stat.S_ISLNK(mode) and
                         stat.S_IFMT(mode) in {0, stat.S_IFREG}, "invalid_entry_type")
                _require(not entry.flag_bits & 1 and entry.compress_type in
                         {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}, "unsupported_entry")
                by_path[name] = entry
            _require({"manifest.json", "data.json"} <= by_path.keys(), "missing_metadata")
            _require(sum(by_path[name].file_size for name in ("manifest.json", "data.json"))
                     <= MAX_METADATA_BYTES, "metadata_too_large")
            manifest = _metadata(archive, by_path["manifest.json"])
            _require(isinstance(manifest, dict) and manifest.get("format") == FORMAT and
                     type(manifest.get("version")) is int and manifest["version"] in TABLES_BY_VERSION,
                     "unsupported_format")
            _require(manifest.get("resume_processing") is False, "unsafe_processing_state")
            timestamp = manifest.get("exported_at")
            _require(isinstance(timestamp, str) and
                     datetime.fromisoformat(timestamp).utcoffset() is not None, "invalid_timestamp")
            owner = manifest.get("owner")
            _require(isinstance(owner, dict), "invalid_owner")
            owner_id = _uuid(owner.get("id"))
            _require(all(isinstance(owner.get(field), str) for field in ("username", "display_name")),
                     "invalid_owner")
            payloads = manifest.get("payloads")
            _require(isinstance(payloads, list) and 1 <= len(payloads) < MAX_ENTRIES,
                     "invalid_payloads")
            declared = {}
            total = by_path["manifest.json"].file_size
            for item in payloads:
                _require(isinstance(item, dict), "invalid_payload")
                name, size, checksum = item.get("path"), item.get("bytes"), item.get("sha256")
                _require(isinstance(name, str) and name != "manifest.json" and
                         name in by_path and name not in declared, "invalid_payload")
                _require(_integer(size) and size >= 0 and
                         isinstance(checksum, str) and re.fullmatch(r"[0-9a-f]{64}", checksum),
                         "invalid_payload")
                total += size
                _require(total <= MAX_TOTAL_BYTES, "payloads_too_large")
                _require(by_path[name].file_size == size, "size_mismatch")
                declared[name] = item
            _require(set(declared) == set(by_path) - {"manifest.json"}, "payload_set_mismatch")
            for name, item in declared.items():
                digest, actual_size = hashlib.sha256(), 0
                with archive.open(by_path[name]) as stream:
                    while chunk := stream.read(CHUNK_BYTES):
                        actual_size += len(chunk)
                        _require(actual_size <= item["bytes"], "size_mismatch")
                        digest.update(chunk)
                _require(actual_size == item["bytes"] and digest.hexdigest() == item["sha256"],
                         "checksum_mismatch")
            data = _metadata(archive, by_path["data.json"])
            tables = TABLES_BY_VERSION[manifest["version"]]
            _require(isinstance(data, dict) and set(data) == {"collections"} and
                     isinstance(data["collections"], dict) and
                     set(data["collections"]) == set(tables), "invalid_collections")
            collections = data["collections"]
            counts = manifest.get("counts")
            _require(isinstance(counts, dict) and set(counts) == set(tables), "invalid_counts")
            for table in tables:
                _require(isinstance(collections[table], list) and _integer(counts[table]) and
                         counts[table] == len(collections[table]), "count_mismatch")
            _references(collections, declared, owner_id)
            return dict(counts)
    except ValidationError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError,
            zipfile.BadZipFile, EOFError, RecursionError, NotImplementedError):
        raise ValidationError("archive_invalid") from None


def main():
    parser = argparse.ArgumentParser(description="离线校验知页账号导出包，不解压、不恢复。")
    parser.add_argument("archive", help="导出 ZIP 文件")
    arguments = parser.parse_args()
    try:
        counts = verify_archive(arguments.archive)
    except ValidationError as error:
        print(f"校验失败：{error}", file=sys.stderr)
        return 1
    print(f"校验成功：{len(counts)} 类数据，共 {sum(counts.values())} 条记录。未执行恢复。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
