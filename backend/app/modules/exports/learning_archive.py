"""Explicit v3 user-content fields and credential-free frozen learning sources.

Operational leases, outbox delivery identities and reminder scan cursors are not
account content. Request fingerprints are retained for durable user receipts;
none of these typed requests contains credentials or provider configuration.
"""
from copy import deepcopy
import re
from app.modules.ai.source_context import public_source
from app.modules.ai.text_context import validate_snapshot
from app.modules.files.models import FileVariant
from app.modules.learning.models import Deck, Card, CardGeneration
from app.modules.learning.quiz_models import (QuizIdentity, QuizQuestion, QuizAttempt, QuizAnswer,
    QuizOperation, QuizAnswerReview, QuizGeneration, QuizGrading)
from app.modules.learning.review_models import ReviewItem, ReviewPlan, ReviewLog
from app.modules.learning.reminder_models import LearningSettings, DailyDigest
from app.modules.learning.event_models import LearningEvent

BASE = ("id", "created_at", "owner_id")
FIELDS = {
    Deck: BASE + ("name", "status", "revision", "updated_at"),
    # Generation before cards: their owner-scoped foreign keys are mandatory.
    CardGeneration: BASE + ("deck_id", "run_id", "request_key", "request_hash", "kind", "count", "source", "status", "error_code", "finished_at"),
    Card: BASE + ("deck_id", "kind", "front", "back", "payload", "source", "status", "revision", "updated_at", "generation_id", "generation_ordinal"),
    QuizIdentity: BASE + ("title", "status", "revision", "question_count", "updated_at"),
    QuizQuestion: BASE + ("quiz_id", "position", "kind", "prompt", "options", "correct_answer", "explanation", "max_points", "source"),
    QuizAttempt: BASE + ("quiz_id", "request_key", "quiz_revision", "title", "snapshot", "status", "revision", "total_points", "max_points", "submitted_at", "finished_at"),
    QuizAnswer: BASE + ("attempt_id", "question_id", "answer", "revision", "status", "points", "feedback", "provenance"),
    QuizOperation: BASE + ("attempt_id", "request_key", "request_hash", "result"),
    QuizAnswerReview: BASE + ("answer_id", "old_grade", "new_grade", "reason", "request_key"),
    QuizGeneration: BASE + ("quiz_id", "run_id", "request_key", "request_hash", "title", "kinds", "count", "source", "status", "error_code", "finished_at"),
    QuizGrading: BASE + ("answer_id", "answer_revision", "attempt_revision", "run_id", "request_key", "request_hash", "input", "status", "result", "error_code", "finished_at"),
    ReviewItem: BASE + ("kind", "note_id", "card_id", "quiz_id"),
    ReviewPlan: BASE + ("item_id", "strategy", "status", "next_due_at", "algorithm_version", "scheduler_state", "last_fsrs_state", "revision", "updated_at"),
    ReviewLog: BASE + ("item_id", "plan_id", "request_key", "request_hash", "action", "rating", "scheduled_for", "reviewed_at", "old_snapshot", "new_snapshot", "reason", "result_plan"),
    LearningSettings: BASE + ("timezone", "review_time", "reminder_enabled", "revision", "updated_at"),
    DailyDigest: BASE + ("kind", "local_date", "timezone", "counts"),
    LearningEvent: BASE + ("event_id", "kind", "note_id", "card_id", "quiz_id", "started_at", "ended_at", "request_hash"),
}


def sources(collections, *, notes, versions, pages, files, reading, internal_keys):
    """Detach storage keys, preserving historical page/file identity in v3.

    A source page need not still be in the current note. Its owned original and
    reading assets must be present, so an archive never advertises a missing file.
    """
    reading_hashes = {}
    def freeze(source):
        if source is None:
            return None
        safe = public_source(source).model_dump(mode="json")
        note = notes[safe["note_id"]]
        if safe["kind"] == "text":
            if note["kind"] != "text":
                raise ValueError("learning_source_kind")
            version = versions[(safe["note_id"], safe["revision"])]["snapshot"]
            if version.get("owner_id") != str(note["owner_id"]):
                raise ValueError("learning_source_owner")
            validate_snapshot(safe, version)
            return safe
        if len(safe["pages"]) != 1 or note["kind"] != "image":
            raise ValueError("learning_source_page_count")
        identity = source["_card_identity"]
        if set(identity) != {"sha256", "width", "height", "reading_sha256"} or any(
            not isinstance(identity[key], str) or not re.fullmatch(r"[0-9a-f]{64}", identity[key])
            for key in ("sha256", "reading_sha256")) or any(
                type(identity[key]) is not int or identity[key] < 1 for key in ("width", "height")):
            raise ValueError("learning_source_identity")
        for original, page in zip(source["pages"], safe["pages"], strict=True):
            file_id = original["file_id"]
            file, variant = files[file_id], reading[file_id]
            actual = pages.get(page["page_id"])
            if (actual and str(actual["note_id"]) != safe["note_id"] or file["status"] != "ready"
                or (file["sha256"], file["width"], file["height"]) != (identity["sha256"], identity["width"], identity["height"])
                or original["object_key"] != internal_keys[(FileVariant, variant["id"])]
                or original["bytes"] != variant["bytes"]):
                raise ValueError("learning_source_assets")
            page.update(file_id=file_id, reading_path=variant["path"])
            expected = identity['reading_sha256']
            if reading_hashes.setdefault(variant['path'],expected)!=expected:
                raise ValueError('learning_reading_hash_conflict')
            if 'page_revision' in original:
                page['page_revision'] = original['page_revision']
        safe["_card_identity"] = deepcopy(identity)
        return safe

    for table in ("learning_cards", "card_generations", "quiz_questions", "quiz_generations"):
        for row in collections[table]:
            row["source"] = freeze(row["source"])
    for row in collections["quiz_attempts"]:
        snapshot = deepcopy(row["snapshot"])
        if not isinstance(snapshot, list) or not 1 <= len(snapshot) <= 50:
            raise ValueError("learning_attempt_snapshot")
        for question in snapshot:
            question["source"] = freeze(question["source"])
        row["snapshot"] = snapshot
    return reading_hashes
