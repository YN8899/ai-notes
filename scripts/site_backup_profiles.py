"""Frozen schema evidence for explicit pre-PDF, pre-OCR and OCR revisions.

The catalog is generated once from a new PostgreSQL 16 database migrated through
344f1b4b059e -> b5ebe7f299de -> 0b3b93086d90 -> c2e41a9f3b67. It contains no
application rows. Its migration hashes use UTF-8 source bytes with CRLF changed
to LF, so an ordinary Windows checkout does not alter the provenance evidence.
The pre-PDF catalog retains eight exact PostgreSQL 16 CHECK dump/restore spellings;
the newer catalogs add three PDF/AI CHECK spellings measured the same way:
the parser moves an array text cast onto each literal when recreating that DDL.
Only these two complete strings per named constraint are accepted, with every
other catalog field compared exactly. No general SQL rewrite is performed.
Never regenerate the expected catalog from a database being backed up.
"""

import json
from pathlib import Path
import re

from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql

from site_backup_support import SiteBackupError


LEGACY_REVISION = "c2e41a9f3b67"
LEGACY_TABLE_NAMES = frozenset({
    "alembic_version", "ai_credentials", "audit_logs", "file_tickets",
    "file_variants", "files", "folders", "invite_codes", "jobs", "note_pages",
    "note_tags", "note_versions", "notebooks", "notes", "notifications",
    "outbox_events", "password_resets", "rate_buckets", "sessions", "tags",
    "upload_sessions", "users",
})
PRE_OCR_REVISION = "2ef16e7ca892"  # c39 application, 27 tables.
OCR_REVISION = "7c0a9d2e4f61"
SEMANTIC_REVISION = "b81d4f0a6c32"
CARD_REVISION = "c4d72e9a1f03"
REVIEW_REVISION = "d58a3b7c901e"
QUIZ_REVISION = "e62a9c4f710b"
REMINDER_REVISION = "f73b5d8a201c"
EVENT_REVISION = "84c6e9b302df"
SOCIAL_REVISION = "a13f25b8c901"
CHAT_REVISION = "b26f36c9d012"
COMMUNITY_REVISION = "c37f47dae123"
M3_REVISION = "d48f58ebf234"
PUSH_REVISION = "e59a69fc0345"
OFFLINE_REVISION = "f6ab70ad1456"
PAGE_RETIREMENT_REVISION = "f7bc81be2567"
TOOLS_REVISION = "07bc81be2567"
PERSONALIZATION_REVISION = "18cd92cf3678"
REALTIME_REVISION = "29de03d04789"
PRE_OCR_TABLE_NAMES = LEGACY_TABLE_NAMES | {
    "pdf_imports", "ai_conversations", "ai_runs", "ai_steps", "ai_saved_notes",
}
OCR_TABLE_NAMES = PRE_OCR_TABLE_NAMES | {"ocr_documents"}
SEMANTIC_TABLE_NAMES = OCR_TABLE_NAMES | {"semantic_indexes", "semantic_chunks", "semantic_queries", "semantic_calls"}
CARD_TABLE_NAMES = SEMANTIC_TABLE_NAMES | {"learning_decks", "learning_cards", "card_generations"}
REVIEW_TABLE_NAMES = CARD_TABLE_NAMES | {"learning_quizzes", "review_items", "review_plans", "review_logs"}
QUIZ_TABLE_NAMES = REVIEW_TABLE_NAMES | {"quiz_questions", "quiz_attempts", "quiz_answers", "quiz_operations",
                                       "quiz_answer_reviews", "quiz_generations", "quiz_gradings"}
REMINDER_TABLE_NAMES = QUIZ_TABLE_NAMES | {"user_learning_settings", "daily_review_digests"}
EVENT_TABLE_NAMES = REMINDER_TABLE_NAMES | {"learning_events"}
SOCIAL_TABLE_NAMES = EVENT_TABLE_NAMES | {"social_profiles", "friendships", "follows", "note_grants"}
CHAT_TABLE_NAMES = SOCIAL_TABLE_NAMES | {"chat_threads", "chat_messages", "chat_read_cursors", "chat_attachments", "realtime_tickets"}
COMMUNITY_TABLE_NAMES = CHAT_TABLE_NAMES | {"community_posts", "community_snapshots", "community_assets", "community_comments", "community_reactions", "community_reports"}
M3_TABLE_NAMES = COMMUNITY_TABLE_NAMES | {"social_archive_records", "social_archive_assets"}
PUSH_TABLE_NAMES = M3_TABLE_NAMES | {"push_subscriptions"}
OFFLINE_TABLE_NAMES = PUSH_TABLE_NAMES | {"offline_sync_receipts", "offline_sync_states"}
TOOLS_TABLE_NAMES = OFFLINE_TABLE_NAMES | {"ai_tool_calls"}
PERSONALIZATION_TABLE_NAMES = TOOLS_TABLE_NAMES | {"user_home_preferences", "learning_analysis_previews", "learning_analyses"}
REALTIME_TABLE_NAMES = PERSONALIZATION_TABLE_NAMES | {"notification_counters"}
FROZEN_TABLE_NAMES = {PRE_OCR_REVISION: PRE_OCR_TABLE_NAMES, OCR_REVISION: OCR_TABLE_NAMES,
                      SEMANTIC_REVISION: SEMANTIC_TABLE_NAMES, CARD_REVISION: CARD_TABLE_NAMES,
                      REVIEW_REVISION: REVIEW_TABLE_NAMES, QUIZ_REVISION: QUIZ_TABLE_NAMES,
                      REMINDER_REVISION: REMINDER_TABLE_NAMES, EVENT_REVISION: EVENT_TABLE_NAMES,
                      SOCIAL_REVISION: SOCIAL_TABLE_NAMES, CHAT_REVISION: CHAT_TABLE_NAMES,
                      COMMUNITY_REVISION: COMMUNITY_TABLE_NAMES, M3_REVISION: M3_TABLE_NAMES,
                      PUSH_REVISION: PUSH_TABLE_NAMES, OFFLINE_REVISION: OFFLINE_TABLE_NAMES,
                      PAGE_RETIREMENT_REVISION: OFFLINE_TABLE_NAMES,
                      TOOLS_REVISION: TOOLS_TABLE_NAMES,
                      PERSONALIZATION_REVISION: PERSONALIZATION_TABLE_NAMES,
                      REALTIME_REVISION: REALTIME_TABLE_NAMES}
LEGACY_CATALOG_PATH = (Path(__file__).resolve().parents[1] / "ops" /
                       "site-backup-profiles" / f"{LEGACY_REVISION}.json")
_MIGRATIONS = frozenset({
    "344f1b4b059e_m0_foundation.py",
    "b5ebe7f299de_m1_notes_organization_and_versions.py",
    "0b3b93086d90_m1_private_files_upload_sessions_and_.py",
    "c2e41a9f3b67_personal_ai_credentials.py",
})
_TABLE_FIELDS = {"columns", "primary_key", "foreign_keys", "unique_constraints",
                 "check_constraints", "indexes"}
_CATALOG_FIELDS = {"format_version", "revision", "postgres_major", "tables",
                   "migration_sources_sha256_lf", "catalog", "check_sqltext_variants"}
_RESTORED_CHECKS = {
    "ai_credentials": {"ai_credential_test_status"},
    "file_variants": {"file_variant_kind"},
    "files": {"file_status"},
    "jobs": {"job_status"},
    "notes": {"note_kind"},
    "upload_sessions": {"upload_status"},
    "users": {"user_role", "user_status"},
}
_FROZEN_RESTORED_CHECKS = {**_RESTORED_CHECKS,
    "pdf_imports": {"pdf_status"}, "ai_runs": {"ai_run_status"}, "ai_steps": {"ai_step_state"}}
_SEMANTIC_RESTORED_CHECKS = {**_FROZEN_RESTORED_CHECKS,
    "semantic_indexes": {"semantic_index_status"}, "semantic_queries": {"semantic_query_status"},
    "semantic_calls": {"semantic_call_status"}}
_CARD_RESTORED_CHECKS = {**_SEMANTIC_RESTORED_CHECKS,
    "learning_decks": {"learning_deck_state"},
    "learning_cards": {"learning_card_state", "learning_card_kind"},
    "card_generations": {"card_generation_state", "card_generation_kind"}}
_REVIEW_RESTORED_CHECKS = {**_CARD_RESTORED_CHECKS,
    "learning_quizzes": {"learning_quiz_state"}, "review_plans": {"review_plan_state"},
    "review_logs": {"review_log_action", "review_log_plan_rating"}}
_QUIZ_RESTORED_CHECKS = {**_REVIEW_RESTORED_CHECKS,
    "quiz_questions": {"quiz_question_kind"}, "quiz_attempts": {"quiz_attempt_state"},
    "quiz_answers": {"quiz_answer_state"}, "quiz_generations": {"quiz_generation_state"},
    "quiz_gradings": {"quiz_grading_state"}}
_SOCIAL_RESTORED_CHECKS = {**_QUIZ_RESTORED_CHECKS,
    "social_profiles": {"social_profile_visibility"}, "friendships": {"friendship_status"}}
_COMMUNITY_RESTORED_CHECKS = {**_SOCIAL_RESTORED_CHECKS,
    "community_posts": {"community_post_status", "community_post_type"},
    "community_snapshots": {"community_snapshot_status", "community_snapshot_type"},
    "community_assets": {"community_asset_status"}, "community_reactions": {"community_reaction_kind"},
    "community_reports": {"community_report_status"}}
_M3_RESTORED_CHECKS = {**_COMMUNITY_RESTORED_CHECKS,
    "social_archive_records": {"social_archive_kind"}, "social_archive_assets": {"social_archive_asset_role"}}
_TOOLS_RESTORED_CHECKS = {**_M3_RESTORED_CHECKS,
    "ai_tool_calls": {"ai_tool_name", "ai_tool_state"}}
_CONSTRAINT_FLAGS = {"deferrable", "initially_deferred", "validated"}


def _require(condition):
    if not condition:
        raise SiteBackupError("backup_schema_invalid")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _canonical(value):
    """Keep only JSON schema metadata; no object repr or server identifiers."""
    if isinstance(value, dict):
        _require(all(isinstance(key, str) for key in value))
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    _require(value is None or type(value) in {bool, int, float} or isinstance(value, str))
    return value


def _ordered(items):
    return sorted(items, key=lambda item: json.dumps(item, sort_keys=True,
                                                    separators=(",", ":"), allow_nan=False))


def capture_vector_catalog(connection):
    """Extension membership and function definitions, independent of app rows.

    Merely excluding extension members would let ALTER EXTENSION ADD hide an
    unrelated routine. Compare a frozen clean-install inventory as well.
    """
    return [dict(row) for row in connection.execute(text("""
        SELECT pg_describe_object(d.classid,d.objid,d.objsubid) AS identity,
               CASE WHEN d.classid='pg_proc'::regclass THEN (
                        SELECT CASE WHEN p.prokind <> 'a' THEN pg_get_functiondef(p.oid)
                          ELSE (SELECT jsonb_build_object(
                            'transition', a.aggtransfn::regprocedure::text,
                            'final', a.aggfinalfn::regprocedure::text,
                            'combine', a.aggcombinefn::regprocedure::text,
                            'serial', a.aggserialfn::regprocedure::text,
                            'deserial', a.aggdeserialfn::regprocedure::text,
                            'moving_transition', a.aggmtransfn::regprocedure::text,
                            'moving_inverse', a.aggminvtransfn::regprocedure::text,
                            'moving_final', a.aggmfinalfn::regprocedure::text,
                            'sort', a.aggsortop::regoperator::text,
                            'transition_type', a.aggtranstype::regtype::text,
                            'moving_type', a.aggmtranstype::regtype::text,
                            'properties', to_jsonb(a) - ARRAY['aggfnoid','aggtransfn','aggfinalfn',
                              'aggcombinefn','aggserialfn','aggdeserialfn','aggmtransfn','aggminvtransfn',
                              'aggmfinalfn','aggsortop','aggtranstype','aggmtranstype'])::text
                            FROM pg_aggregate a WHERE a.aggfnoid=p.oid) END
                        FROM pg_proc p WHERE p.oid=d.objid)
                    WHEN d.classid='pg_operator'::regclass THEN (
                        SELECT o.oprcode::regprocedure::text FROM pg_operator o WHERE o.oid=d.objid)
                    ELSE NULL END AS definition
        FROM pg_depend d JOIN pg_extension e ON e.oid=d.refobjid
        WHERE d.refclassid='pg_extension'::regclass AND d.deptype='e' AND e.extname='vector'
        ORDER BY pg_describe_object(d.classid,d.objid,d.objsubid) COLLATE "C"
    """)).mappings()]


def validate_vector_catalog(connection):
    try:
        path = LEGACY_CATALOG_PATH.with_name("vector-0.8.7.json")
        expected = json.loads(path.read_bytes(), object_pairs_hook=_pairs)
        _require(expected == capture_vector_catalog(connection))
    except SiteBackupError:
        raise
    except Exception:
        raise SiteBackupError("backup_schema_invalid") from None


def capture_learning_guards(connection):
    """Public routines except the separately pinned vector members, plus triggers.

    A routine attached to another allowed extension (such as plpgsql) is still
    an unexpected application-schema object. Do not let membership hide it.
    """
    routines = [dict(row) for row in connection.execute(text("""
        SELECT p.oid::regprocedure::text AS identity, pg_get_functiondef(p.oid) AS definition
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND NOT EXISTS (
            SELECT 1 FROM pg_depend d JOIN pg_extension e ON e.oid=d.refobjid
            WHERE d.classid='pg_proc'::regclass AND d.refclassid='pg_extension'::regclass
            AND d.objid=p.oid AND d.deptype='e' AND e.extname='vector')
        ORDER BY p.oid::regprocedure::text COLLATE "C"
    """)).mappings()]
    triggers = [dict(row) for row in connection.execute(text("""
        SELECT c.relname AS table_name, t.tgname AS name, t.tgenabled AS enabled,
               pg_get_triggerdef(t.oid, true) AS definition
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND NOT t.tgisinternal
        ORDER BY c.relname COLLATE "C", t.tgname COLLATE "C"
    """)).mappings()]
    return {"routines": routines, "triggers": triggers}


def validate_learning_guards(connection, revision):
    try:
        _require(revision in FROZEN_TABLE_NAMES and "review_logs" in FROZEN_TABLE_NAMES[revision])
        path = LEGACY_CATALOG_PATH.with_name(f"{revision}-guards.json")
        expected = json.loads(path.read_bytes(), object_pairs_hook=_pairs)
        _require(isinstance(expected, dict) and set(expected) == {"routines", "triggers"})
        _require(expected == capture_learning_guards(connection))
    except SiteBackupError:
        raise
    except Exception:
        raise SiteBackupError("backup_schema_invalid") from None


def legacy_catalog():
    """Load a fresh copy of the checked-in catalog, never inspect a source DB."""
    try:
        raw = LEGACY_CATALOG_PATH.read_bytes()
        _require(len(raw) <= 1024 * 1024)
        document = json.loads(raw, object_pairs_hook=_pairs)
        _require(isinstance(document, dict) and set(document) == _CATALOG_FIELDS)
        _require(type(document["format_version"]) is int and document["format_version"] == 1)
        _require(document["revision"] == LEGACY_REVISION)
        _require(type(document["postgres_major"]) is int and document["postgres_major"] == 16)
        _require(document["tables"] == sorted(LEGACY_TABLE_NAMES))
        sources = document["migration_sources_sha256_lf"]
        _require(isinstance(sources, dict) and set(sources) == _MIGRATIONS
                 and all(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
                         for digest in sources.values()))
        catalog = document["catalog"]
        _require(isinstance(catalog, dict) and set(catalog) == LEGACY_TABLE_NAMES)
        for table in catalog.values():
            _require(isinstance(table, dict) and set(table) == _TABLE_FIELDS)
            _require(isinstance(table["columns"], list) and bool(table["columns"]))
            names = []
            for column in table["columns"]:
                _require(isinstance(column, dict)
                         and set(column) == {"name", "type", "nullable", "default", "identity", "computed"}
                         and isinstance(column["name"], str) and bool(column["name"])
                         and isinstance(column["type"], str) and bool(column["type"])
                         and type(column["nullable"]) is bool
                         and (column["default"] is None or isinstance(column["default"], str))
                         and all(column[field] is None or isinstance(column[field], dict)
                                 for field in ("identity", "computed")))
                names.append(column["name"])
            _require(names == sorted(set(names)))
            primary = table["primary_key"]
            _require(isinstance(primary, dict)
                     and set(primary) == {"name", "columns", "flags", "dialect_options"}
                     and (primary["name"] is None or isinstance(primary["name"], str))
                     and isinstance(primary["columns"], list) and bool(primary["columns"])
                     and isinstance(primary["dialect_options"], dict)
                     and all(column in names for column in primary["columns"]))
            _flags_valid(primary["flags"])
            for field in _TABLE_FIELDS - {"columns", "primary_key"}:
                _require(isinstance(table[field], list)
                         and all(isinstance(item, dict) for item in table[field]))
            for constraint in table["foreign_keys"] + table["unique_constraints"]:
                _flags_valid(constraint.get("flags"))
        variants = document["check_sqltext_variants"]
        _require(isinstance(variants, dict) and set(variants) == set(_RESTORED_CHECKS))
        for table, constraints in _RESTORED_CHECKS.items():
            _require(isinstance(variants[table], dict) and set(variants[table]) == constraints)
            for name in constraints:
                pair = variants[table][name]
                originals = [item["sqltext"] for item in catalog[table]["check_constraints"]
                             if item.get("name") == name]
                _require(len(originals) == 1 and isinstance(pair, list) and len(pair) == 2
                         and all(isinstance(value, str) and bool(value) for value in pair)
                         and len(set(pair)) == 2 and pair[0] == originals[0])
        return document
    except SiteBackupError:
        raise
    except Exception:
        raise SiteBackupError("backup_schema_invalid") from None


def _flags_valid(flags):
    _require(isinstance(flags, dict) and set(flags) == _CONSTRAINT_FLAGS
             and all(type(value) is bool for value in flags.values()))


def capture_catalog(connection, table_names):
    """Reflect deterministic public-table DDL metadata, without querying rows."""
    try:
        names = set(table_names)
        _require(bool(names) and all(isinstance(name, str)
                                    and re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in names))
        inspector = inspect(connection)
        # Reflection omits FK validation and PK/UNIQUE deferral semantics.
        # Read these structural flags directly; never query application rows.
        constraint_flags = {(row["table_name"], row["constraint_name"]): {
            field: row[field] for field in _CONSTRAINT_FLAGS} for row in
            connection.execute(text("""
                SELECT r.relname AS table_name, c.conname AS constraint_name,
                       c.condeferrable AS deferrable, c.condeferred AS initially_deferred,
                       c.convalidated AS validated
                FROM pg_catalog.pg_constraint c
                JOIN pg_catalog.pg_class r ON r.oid=c.conrelid
                JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace
                WHERE n.nspname='public' AND c.contype IN ('p','u','f')
            """)).mappings()}
        dialect = postgresql.dialect()
        catalog = {}
        for name in sorted(names):
            columns = [{
                "name": column["name"],
                "type": column["type"].compile(dialect=dialect),
                "nullable": column["nullable"],
                "default": column.get("default"),
                "identity": _canonical(column.get("identity")),
                "computed": _canonical(column.get("computed")),
            } for column in inspector.get_columns(name, schema="public")]
            primary = inspector.get_pk_constraint(name, schema="public")
            foreign = [{
                "name": item.get("name"),
                "columns": list(item["constrained_columns"]),
                "referred_schema": item.get("referred_schema") or "public",
                "referred_table": item["referred_table"],
                "referred_columns": list(item["referred_columns"]),
                "options": _canonical(item.get("options", {})),
                "flags": constraint_flags[(name, item["name"])],
            } for item in inspector.get_foreign_keys(name, schema="public")]
            unique = [{
                "name": item.get("name"),
                "columns": list(item["column_names"]),
                "dialect_options": _canonical(item.get("dialect_options", {})),
                "flags": constraint_flags[(name, item["name"])],
            } for item in inspector.get_unique_constraints(name, schema="public")]
            checks = [{
                "name": item.get("name"), "sqltext": item["sqltext"],
                "dialect_options": _canonical(item.get("dialect_options", {})),
            } for item in inspector.get_check_constraints(name, schema="public")]
            indexes = [{
                "name": item["name"], "unique": item["unique"],
                "columns": list(item["column_names"]),
                "expressions": _canonical(item.get("expressions")),
                "column_sorting": _canonical(item.get("column_sorting", {})),
                "dialect_options": _canonical(item.get("dialect_options", {})),
            } for item in inspector.get_indexes(name, schema="public")]
            catalog[name] = {
                "columns": sorted(columns, key=lambda item: item["name"]),
                "primary_key": {"name": primary.get("name"),
                                "columns": list(primary.get("constrained_columns", [])),
                                "dialect_options": _canonical(primary.get("dialect_options", {})),
                                "flags": constraint_flags[(name, primary["name"])]},
                "foreign_keys": _ordered(foreign),
                "unique_constraints": _ordered(unique),
                "check_constraints": _ordered(checks),
                "indexes": _ordered(indexes),
            }
        return _canonical(catalog)
    except SiteBackupError:
        raise
    except Exception:
        raise SiteBackupError("backup_schema_invalid") from None


def validate_legacy_schema(connection):
    """Reject a c2 stamp hiding any different table or reflected DDL metadata."""
    try:
        document = legacy_catalog()
        _require(set(inspect(connection).get_table_names(schema="public")) == LEGACY_TABLE_NAMES)
        catalog = capture_catalog(connection, LEGACY_TABLE_NAMES)
        for table, constraints in document["check_sqltext_variants"].items():
            for check in catalog[table]["check_constraints"]:
                pair = constraints.get(check.get("name"))
                if pair is not None and check.get("sqltext") in pair:
                    check["sqltext"] = pair[0]
        _require(catalog == document["catalog"])
    except SiteBackupError:
        raise
    except Exception:
        raise SiteBackupError("backup_schema_invalid") from None


def frozen_catalog(revision):
    """Independent, migration-only schema evidence for supported newer heads."""
    try:
        _require(revision in FROZEN_TABLE_NAMES)
        path = LEGACY_CATALOG_PATH.with_name(f"{revision}.json")
        raw = path.read_bytes()
        _require(len(raw) <= 1024 * 1024)
        document = json.loads(raw, object_pairs_hook=_pairs)
        _require(isinstance(document, dict) and set(document) == _CATALOG_FIELDS)
        _require(type(document["format_version"]) is int and document["format_version"] == 1
                 and document["revision"] == revision
                 and type(document["postgres_major"]) is int and document["postgres_major"] == 16)
        names = FROZEN_TABLE_NAMES[revision]
        _require(document["tables"] == sorted(names)
                 and isinstance(document["catalog"], dict) and set(document["catalog"]) == names)
        _require(all(isinstance(table, dict) and set(table) == _TABLE_FIELDS
                     for table in document["catalog"].values()))
        variants = document["check_sqltext_variants"]
        checks = (_TOOLS_RESTORED_CHECKS if revision in {TOOLS_REVISION, PERSONALIZATION_REVISION, REALTIME_REVISION} else
                  _M3_RESTORED_CHECKS if revision in {M3_REVISION, PUSH_REVISION, OFFLINE_REVISION, PAGE_RETIREMENT_REVISION} else
                  _COMMUNITY_RESTORED_CHECKS if revision == COMMUNITY_REVISION else
                  _SOCIAL_RESTORED_CHECKS if revision in {SOCIAL_REVISION, CHAT_REVISION} else
                  _QUIZ_RESTORED_CHECKS if revision in {QUIZ_REVISION, REMINDER_REVISION, EVENT_REVISION} else
                  _REVIEW_RESTORED_CHECKS if revision == REVIEW_REVISION else
                  _CARD_RESTORED_CHECKS if revision == CARD_REVISION else
                  _SEMANTIC_RESTORED_CHECKS if revision == SEMANTIC_REVISION else _FROZEN_RESTORED_CHECKS)
        _require(isinstance(variants, dict) and set(variants) == set(checks))
        for table, constraints in checks.items():
            _require(isinstance(variants[table], dict) and set(variants[table]) == constraints)
            for name in constraints:
                pair = variants[table][name]
                originals = [item["sqltext"] for item in document["catalog"][table]["check_constraints"]
                             if item.get("name") == name]
                _require(len(originals) == 1 and isinstance(pair, list) and len(pair) == 2
                         and all(isinstance(value, str) and bool(value) for value in pair)
                         and len(set(pair)) == 2 and pair[0] == originals[0])
        sources = document["migration_sources_sha256_lf"]
        _require(isinstance(sources, dict) and bool(sources)
                 and all(isinstance(name, str) and re.fullmatch(r"[0-9a-z_]+\.py", name)
                         and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
                         for name, digest in sources.items()))
        return document
    except SiteBackupError:
        raise
    except Exception:
        raise SiteBackupError("backup_schema_invalid") from None


def validate_frozen_schema(connection, revision):
    """A correct revision stamp cannot hide drifted columns, FKs or checks."""
    try:
        document = frozen_catalog(revision)
        names = FROZEN_TABLE_NAMES[revision]
        _require(set(inspect(connection).get_table_names(schema="public")) == names)
        catalog = capture_catalog(connection, names)
        for table, constraints in document["check_sqltext_variants"].items():
            for check in catalog[table]["check_constraints"]:
                pair = constraints.get(check.get("name"))
                if pair is not None and check.get("sqltext") in pair:
                    check["sqltext"] = pair[0]
        _require(catalog == document["catalog"])
    except SiteBackupError:
        raise
    except Exception:
        raise SiteBackupError("backup_schema_invalid") from None
