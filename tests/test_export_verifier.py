"""Archive verification uses generated files only: no database or live services."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID
import warnings
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_export", ROOT / "scripts/verify_export.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def identity(number):
    return str(UUID(int=number))


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def legacy_ai(tables):
    tables=copy.deepcopy(tables)
    for run in tables["ai_runs"]:
        for field in ("provider","provider_snapshot","tools","tool_scope"):
            run.pop(field)
    return tables


class ExportVerifierTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.archive = Path(self.directory.name) / "private-account.zip"
        self.owner = identity(1)
        self.tables = {table: [] for table in verifier.TABLES}

        def row(table, number, **values):
            data = {"id": identity(number), **values}
            if table in verifier.OWNED_TABLES:
                data["owner_id"] = self.owner
            self.tables[table].append(data)
            return data

        row("folders", 2, parent_id=None)
        row("notebooks", 3, folder_id=identity(2))
        row("tags", 4)
        row("notes", 5, notebook_id=identity(3), body_json={"type": "doc", "content": []})
        row("notes", 6, notebook_id=None, deleted_at="2026-10-20T00:00:00Z")
        self.tables["note_tags"] = [{"owner_id": self.owner, "note_id": identity(5), "tag_id": identity(4)}]
        # Historical organization IDs may no longer exist; they are not live FKs.
        row("note_versions", 7, note_id=identity(5), revision=1,
            snapshot={"notebook_id": identity(999), "tag_ids": [identity(998)],
                      "body_json": {"type": "doc", "content": []}})
        original = f"objects/{identity(8)}/original"
        reading = f"objects/{identity(8)}/variants/{identity(9)}"
        row("files", 8, original_path=original, status="ready", bytes=8)
        row("file_variants", 9, file_id=identity(8), kind="reading", path=reading, bytes=7)
        row("note_pages", 10, note_id=identity(5), file_id=identity(8), position=0, deleted_at=None)
        row("pdf_imports", 11, note_id=identity(5), source_file_id=identity(8))
        row("ai_conversations", 12)
        source = {"note_id": identity(5), "title": "合成来源", "revision": 1,
                  "kind": "image", "mode": "region", "pages": [{
                      "page_id": identity(10), "file_id": identity(8), "number": 1,
                      "reading_path": reading, "region": {"x": 0.5, "y": 0, "width": 0.5, "height": 1}}]}
        row("ai_runs", 13, created_at="2026-10-20T00:00:00Z", conversation_id=identity(12), model="deepseek-flash",
            prompt="合成问题", source=source, answer="合成回答", status="succeeded", usage=None, error_code=None,
            finished_at="2026-10-20T00:00:00Z", progress=None, provider="deepseek", provider_snapshot=None, tools=[], tool_scope=None)
        row("ai_steps", 14, created_at="2026-10-20T00:00:00Z", run_id=identity(13), position=0, kind="answer", pages=[1],
            status="succeeded", answer="合成回答", usage=None, error_code=None)
        row("ai_saved_notes", 15, note_id=identity(6), run_id=identity(13), source=copy.deepcopy(source))
        self.assets = {original: b"original", reading: b"reading"}

    def write(self, *, tables=None, assets=None, manifest_change=None, extra=None):
        tables = self.tables if tables is None else tables
        contents = {"data.json": encode({"collections": tables}),
                    **(self.assets if assets is None else assets)}
        manifest = {
            "format": verifier.FORMAT, "version": verifier.VERSION,
            "exported_at": "2026-10-20T00:00:00Z",
            "owner": {"id": self.owner, "username": "synthetic", "display_name": "合成成员"},
            "counts": {table: len(items) for table, items in tables.items()},
            "resume_processing": False,
            "payloads": [{"path": name, "bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
                         for name, value in contents.items()],
        }
        if manifest_change:
            manifest_change(manifest)
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", encode(manifest))
            for name, value in contents.items():
                archive.writestr(name, value)
            if extra:
                for name, value in extra:
                    archive.writestr(name, value)
        return manifest

    def reject(self, code=None):
        with self.assertRaises(verifier.ValidationError) as failure:
            verifier.verify_archive(self.archive)
        if code:
            self.assertEqual(str(failure.exception), code)

    def test_complete_archive_and_historical_removed_organization_pass(self):
        self.write()
        counts = verifier.verify_archive(self.archive)
        self.assertEqual(counts["notes"], 2)
        self.assertEqual(counts["ai_saved_notes"], 1)
        self.assertEqual(sum(counts.values()), 15)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [self.archive])

    def test_empty_account_passes(self):
        self.write(tables={table: [] for table in verifier.TABLES}, assets={})
        self.assertEqual(sum(verifier.verify_archive(self.archive).values()), 0)

    def test_expired_unreceived_upload_can_have_no_original(self):
        item = self.tables["files"][0]
        item.update(status="expired", original_path=None, original_unavailable_reason="upload_expired")
        self.assets.pop(f"objects/{identity(8)}/original")
        self.write()
        verifier.verify_archive(self.archive)
        item["status"] = "ready"
        self.write()
        self.reject("missing_asset")

    def test_content_tampering_and_declared_size_are_rejected(self):
        self.write(manifest_change=lambda value: value["payloads"][1].update(sha256="0" * 64))
        self.reject("checksum_mismatch")
        self.write(manifest_change=lambda value: value["payloads"][1].update(bytes=999))
        self.reject("size_mismatch")

    def test_intentionally_discarded_rejected_image_original_is_explicit(self):
        rejected = {"id": identity(16), "owner_id": self.owner, "status": "rejected",
                    "mime": "image/png", "original_path": None,
                    "original_unavailable_reason": "image_rejected"}
        self.tables["files"].append(rejected)
        self.write()
        verifier.verify_archive(self.archive)
        for field, value in (("mime", "application/pdf"), ("status", "ready"),
                             ("original_unavailable_reason", "unknown")):
            with self.subTest(field=field):
                tables = copy.deepcopy(self.tables)
                tables["files"][-1][field] = value
                self.write(tables=tables)
                self.reject("missing_asset")

    def test_asset_metadata_must_match_checksummed_payload(self):
        for table, field, value in (("files", "bytes", 99), ("files", "sha256", "0" * 64),
                                    ("file_variants", "bytes", 99)):
            with self.subTest(table=table, field=field):
                tables = copy.deepcopy(self.tables)
                tables[table][0][field] = value
                self.write(tables=tables)
                self.reject("asset_metadata_mismatch")

    def test_missing_or_unlisted_payload_is_rejected(self):
        self.write(manifest_change=lambda value: value["payloads"].pop())
        self.reject("payload_set_mismatch")
        self.write(manifest_change=lambda value: value["payloads"].append({
            "path": f"objects/{identity(99)}/original", "bytes": 0, "sha256": "0" * 64}))
        self.reject("invalid_payload")
        original = f"objects/{identity(8)}/original"
        self.write(assets={name: value for name, value in self.assets.items() if name != original})
        self.reject("missing_asset")

    def test_missing_fixed_metadata_is_rejected(self):
        for missing in ("manifest.json", "data.json"):
            with self.subTest(missing=missing):
                with zipfile.ZipFile(self.archive, "w") as archive:
                    archive.writestr("data.json" if missing == "manifest.json" else "manifest.json", b"{}")
                    archive.writestr(f"objects/{identity(8)}/original", b"original")
                self.reject("missing_metadata")

    def test_unsafe_or_extra_archive_paths_are_rejected(self):
        paths = ("../private.txt", "/data.json", "C:/private.txt", "objects\\private.txt",
                 "objects/../private.txt", "objects/", "unexpected.txt")
        for name in paths:
            with self.subTest(name=name):
                self.write(extra=[(name, b"private")])
                self.reject("unsafe_entry_path")

    def test_duplicate_zip_entry_and_manifest_payload_are_rejected(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            self.write(extra=[("data.json", b"{}")])
        self.reject("duplicate_entry")
        self.write(manifest_change=lambda value: value["payloads"].append(value["payloads"][0]))
        self.reject("invalid_payload")

    def test_symlink_entry_is_rejected(self):
        link = zipfile.ZipInfo(f"objects/{identity(99)}/original")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.write(extra=[(link, b"private-target")])
        self.reject("invalid_entry_type")

    def test_limits_are_checked_before_accepting_archive(self):
        self.write()
        for setting, limit, code in (
            ("MAX_TOTAL_BYTES", 1, "payloads_too_large"),
            ("MAX_METADATA_BYTES", 1, "metadata_too_large"),
            ("MAX_ENTRIES", 2, "invalid_entry_count"),
            ("MAX_ROWS", 1, "too_many_rows"),
        ):
            with self.subTest(setting=setting), patch.object(verifier, setting, limit):
                self.reject(code)
        with zipfile.ZipFile(self.archive) as archive:
            metadata_sizes = [archive.getinfo(name).file_size for name in ("manifest.json", "data.json")]
        with patch.object(verifier, "MAX_METADATA_BYTES", max(metadata_sizes)):
            self.reject("metadata_too_large")

    def test_unsupported_version_and_unsafe_processing_are_rejected(self):
        self.write(manifest_change=lambda value: value.update(version=6))
        self.reject("unsupported_format")
        self.write(manifest_change=lambda value: value.update(version=True))
        self.reject("unsupported_format")
        self.write(manifest_change=lambda value: value.update(resume_processing=True))
        self.reject("unsafe_processing_state")

    def test_v1_keeps_exact_legacy_collection_contract(self):
        tables = {name: rows for name, rows in self.tables.items() if name in verifier.V1_TABLES}
        tables=legacy_ai(tables)
        self.write(tables=tables, manifest_change=lambda manifest: manifest.update(version=1))
        self.assertEqual(set(verifier.verify_archive(self.archive)), set(verifier.V1_TABLES))
        self.write(manifest_change=lambda manifest: manifest.update(version=1))
        self.reject("invalid_collections")

    def test_v2_keeps_ocr_and_excludes_v3_learning_collections(self):
        tables={name:rows for name,rows in self.tables.items() if name in verifier.V2_TABLES}
        tables=legacy_ai(tables)
        self.write(tables=tables,manifest_change=lambda manifest:manifest.update(version=2))
        self.assertEqual(set(verifier.verify_archive(self.archive)),set(verifier.V2_TABLES))
        self.write(manifest_change=lambda manifest:manifest.update(version=2))
        self.reject('invalid_collections')

    def test_v3_keeps_learning_without_v4_social_history(self):
        tables = {name: rows for name, rows in self.tables.items() if name in verifier.V3_TABLES}
        tables=legacy_ai(tables)
        self.write(tables=tables, manifest_change=lambda manifest: manifest.update(version=3))
        self.assertEqual(set(verifier.verify_archive(self.archive)), set(verifier.V3_TABLES))
        self.write(manifest_change=lambda manifest: manifest.update(version=3))
        self.reject("invalid_collections")

    def test_v4_legacy_pages_default_active_but_cannot_smuggle_retirement(self):
        self.tables=legacy_ai({name:rows for name,rows in self.tables.items() if name in verifier.V4_TABLES})
        for page in self.tables["note_pages"]:
            page.pop("deleted_at")
        self.write(manifest_change=lambda manifest: manifest.update(version=4))
        self.assertEqual(set(verifier.verify_archive(self.archive)), set(verifier.V4_TABLES))
        self.tables["note_pages"][0]["deleted_at"] = "2026-11-01T08:00:00+00:00"
        self.write(manifest_change=lambda manifest: manifest.update(version=4))
        self.reject("invalid_page")

    def test_v5_requires_aware_page_retirement_marker(self):
        self.tables["note_pages"][0]["deleted_at"] = "2026-11-01T08:00:00+00:00"
        self.write()
        self.assertEqual(verifier.verify_archive(self.archive)["note_pages"], 1)
        for invalid in (True, 123, "2026-11-01T08:00:00"):
            self.tables["note_pages"][0]["deleted_at"] = invalid
            self.write()
            self.reject("invalid_page")

    def social_history(self):
        stamp = "2026-11-01T08:00:00+00:00"
        peer = {"id": identity(99), "username": "friend", "display_name": "好友"}
        def record(number, kind, source, **payload):
            row = dict(id=identity(number), owner_id=self.owner, created_at=stamp, kind=kind,
                       source_id=identity(source), payload=dict(offline_only=True, **payload))
            self.tables["social_archive_records"].append(row)
            return row
        record(100, "chat_thread", 200, peer=peer, last_seq=1, last_read_seq=0, peer_read_seq=1)
        message = record(101, "chat_message", 201, thread_id=identity(200), seq=1, sender=peer,
                         sender_is_self=False, body="对方发来的完整内容", client_message_id="message-1", attachment_ids=[identity(202)])
        content = b"received-private-attachment"
        path = f"objects/{identity(102)}/original"
        self.tables["social_archive_assets"].append(dict(id=identity(102), owner_id=self.owner,
            created_at=stamp, record_id=message["id"], source_id=identity(202), role="attachment",
            filename="notes.txt", mime="text/plain", bytes=len(content), sha256=hashlib.sha256(content).hexdigest(), path=path))
        self.assets[path] = content
        return message

    def test_v4_participant_history_and_received_attachment_are_valid(self):
        self.social_history()
        self.write()
        self.assertEqual(verifier.verify_archive(self.archive)["social_archive_assets"], 1)

    def test_social_history_cannot_be_an_active_grant_or_contain_unknown_fields(self):
        row = self.social_history()
        row["payload"]["offline_only"] = False
        self.write()
        self.reject("unsafe_social_history")
        row["payload"]["offline_only"] = True
        row["payload"]["session_token"] = "must-never-be-restored"
        self.write()
        self.reject("unsafe_social_history")

    def test_social_history_foreign_owner_and_omitted_attachment_rejected(self):
        self.social_history()
        asset = self.tables["social_archive_assets"][0]
        asset["owner_id"] = identity(99)
        self.write()
        self.reject("owner_mismatch")
        asset["owner_id"] = self.owner
        self.tables["social_archive_assets"].clear()
        self.assets.pop(asset["path"])
        self.write()
        self.reject("missing_social_asset")

    def test_social_asset_metadata_must_match_manifest_hash(self):
        self.social_history()
        self.tables["social_archive_assets"][0]["sha256"] = "0" * 64
        self.write()
        self.reject("social_asset_mismatch")

    def test_social_message_requires_exported_thread_and_true_sender_identity(self):
        row = self.social_history()
        row["payload"]["sender_is_self"] = True
        self.write()
        self.reject("invalid_social_sender")
        row["payload"]["sender_is_self"] = False
        self.tables["social_archive_records"].pop(0)
        self.write()
        self.reject("missing_social_thread")

    def ocr(self):
        self.tables["notes"][0]["kind"] = "image"
        self.tables["ai_runs"][0]["status"] = "succeeded"
        self.tables["ai_runs"][0]["source"]["pages"][0]["page_revision"] = 1
        row = {"id": identity(16), "owner_id": self.owner,
               "created_at": "2026-10-31T00:00:00Z", "updated_at": "2026-10-31T00:00:01Z",
               "note_id": identity(5), "page_id": identity(10), "file_id": identity(8), "run_id": identity(13),
               "source_sha256": None, "source_width": 100, "source_height": 80,
               "corrected_text": "人工纠错 $x^2$", "reviewed": True, "revision": 2}
        self.tables["ocr_documents"].append(row)
        return row

    def test_v2_preserves_ocr_corrections_and_historical_removed_page(self):
        self.ocr()
        # Remove the ordinary saved-note reference: only OCR supports missing
        # historical pages. Its owned file/reading payload must still exist.
        self.tables["ai_saved_notes"] = []
        self.tables["note_pages"] = []
        self.write()
        self.assertEqual(verifier.verify_archive(self.archive)["ocr_documents"], 1)

    def test_ocr_rejects_foreign_missing_malformed_and_internal_metadata(self):
        row = self.ocr()
        cases = [("owner_id", identity(999)), ("file_id", identity(999)), ("run_id", identity(999)),
                 ("page_id", identity(999)), ("revision", True), ("reviewed", 1),
                 ("source_sha256", "bad"), ("source_width", -1), ("source_height", None),
                 ("corrected_text", "x" * 64001), ("corrected_text", None), ("corrected_text", "a\x00b"),
                 ("request_hash", "internal"), ("created_at", "2026-01-01")]
        for field, value in cases:
            with self.subTest(field=field):
                previous = dict(row)
                row[field] = value
                self.write()
                self.reject()
                row.clear()
                row.update(previous)
        self.tables["ai_runs"][0]["status"] = "running"
        self.write()
        self.reject("unsafe_processing_state")

    def test_historical_ocr_exception_does_not_weaken_ordinary_saved_sources(self):
        self.ocr()
        self.tables["note_pages"] = []
        # IDs from distinct tables may collide. A saved-note ID equal to an
        # OCR run ID must never inherit that run's historical-page exception.
        self.tables["ai_saved_notes"][0]["id"] = identity(13)
        self.write()
        self.reject("missing_reference")

    def test_counts_owner_and_duplicate_rows_are_rejected(self):
        self.write(manifest_change=lambda value: value["counts"].update(notes=99))
        self.reject("count_mismatch")
        self.tables["notes"][0]["owner_id"] = identity(99)
        self.write()
        self.reject("owner_mismatch")
        self.tables["notes"][0]["owner_id"] = self.owner
        self.tables["notes"].append(copy.deepcopy(self.tables["notes"][0]))
        self.write()
        self.reject("duplicate_record")

    def test_live_foreign_key_and_ai_source_mismatch_are_rejected(self):
        mutations = [
            ("notebooks", "folder_id"), ("notes", "notebook_id"), ("note_tags", "tag_id"),
            ("note_versions", "note_id"), ("note_pages", "file_id"), ("file_variants", "file_id"),
            ("pdf_imports", "source_file_id"), ("ai_runs", "conversation_id"),
            ("ai_steps", "run_id"), ("ai_saved_notes", "run_id"),
        ]
        for table, field in mutations:
            with self.subTest(table=table):
                tables = copy.deepcopy(self.tables)
                tables[table][0][field] = identity(999)
                self.write(tables=tables)
                self.reject("missing_reference")
        self.tables["ai_runs"][0]["source"]["pages"][0]["reading_path"] = "private-object-key"
        self.write()
        self.reject("source_mismatch")

    def test_folder_cycle_is_rejected(self):
        self.tables["folders"][0]["parent_id"] = identity(16)
        self.tables["folders"].append({"id": identity(16), "owner_id": self.owner, "parent_id": identity(2)})
        self.write()
        self.reject("invalid_folder_parent")

    def test_unreferenced_but_checksummed_object_is_rejected(self):
        self.assets[f"objects/{identity(99)}/original"] = b"unused"
        self.write()
        self.reject("unreferenced_asset")

    def test_malformed_zip_and_duplicate_json_key_have_safe_errors(self):
        self.archive.write_bytes(b"private-note-content-not-a-zip")
        self.reject("archive_invalid")
        self.write()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            # Replace via a fresh ZIP; duplicate JSON keys, not duplicate ZIP entries.
            with zipfile.ZipFile(self.archive) as archive:
                entries = [(item.filename, archive.read(item)) for item in archive.infolist()]
            with zipfile.ZipFile(self.archive, "w") as archive:
                for name, data in entries:
                    archive.writestr(name, b'{"format":"private-note","format":"ai-notes-export"}'
                                     if name == "manifest.json" else data)
        self.reject("duplicate_json_key")

    def test_cli_reports_result_without_private_paths_or_content(self):
        self.write()
        command = [sys.executable, str(ROOT / "scripts/verify_export.py"), str(self.archive)]
        result = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn(str(self.archive), result.stdout + result.stderr)
        self.assertNotIn("synthetic", result.stdout + result.stderr)
        self.archive.write_bytes(b"private-note-content")
        result = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(str(self.archive), result.stdout + result.stderr)
        self.assertNotIn("private-note-content", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
