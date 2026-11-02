"""Metadata-only, transaction-bound invalidation. Never commit here."""
import json
from uuid import UUID

CHANNEL = "notes_realtime_v1"
KINDS = frozenset({"chat", "notifications"})


def encode(owner_id, kind):
    if kind not in KINDS:
        raise ValueError("signal_kind")
    return json.dumps({"v": 1, "owner": str(UUID(str(owner_id))), "kind": kind}, separators=(",", ":"))


def decode(value):
    if not isinstance(value, str) or len(value.encode("utf-8")) > 256:
        return None
    try:
        data = json.loads(value)
        if not isinstance(data, dict) or set(data) != {"v", "owner", "kind"} or type(data["v"]) is not int or data["v"] != 1 or data["kind"] not in KINDS:
            return None
        return UUID(data["owner"]), data["kind"]
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def publish(db, owner_id, kind):
    from sqlalchemy import text
    # PostgreSQL delivers only if this SAME transaction commits. No raw notify
    # bodies are logged; callers can roll back all effects atomically.
    db.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": encode(owner_id, kind)})
