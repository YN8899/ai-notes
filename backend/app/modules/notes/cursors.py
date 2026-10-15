import base64
import binascii
from datetime import datetime, timedelta
import hashlib
import json
import re
from uuid import UUID
from app.shared.db import utcnow
from app.shared.errors import AppError


def scope_key(*values):
    return hashlib.sha256(json.dumps(values, default=str, separators=(",", ":")).encode()).hexdigest()


def encode_cursor(data):
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")


def invalid_cursor():
    return AppError(422, "cursor_invalid", "分页游标无效，请重新加载列表")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate cursor key")
        result[key] = value
    return result


def decode_cursor(token, keys, scope):
    try:
        if not token or len(token) > 1024 or re.fullmatch(r"[A-Za-z0-9_-]+", token) is None:
            raise ValueError("Invalid token")
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        data = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(data, dict) or set(data) != keys or type(data["v"]) is not int or data["v"] != 1 or data["scope"] != scope:
            raise ValueError("Invalid cursor scope")
        return data
    except (ValueError, TypeError, UnicodeError, binascii.Error, RecursionError):
        raise invalid_cursor() from None


def note_position(token, scope):
    data = decode_cursor(token, {"v", "scope", "cutoff", "at", "id"}, scope)
    try:
        cutoff, at = datetime.fromisoformat(data["cutoff"]), datetime.fromisoformat(data["at"])
        note_id = UUID(data["id"])
        if cutoff.utcoffset() != timedelta(0) or at.utcoffset() != timedelta(0) or cutoff.isoformat() != data["cutoff"] or at.isoformat() != data["at"] or str(note_id) != data["id"] or at > cutoff or cutoff > utcnow():
            raise ValueError("Invalid position")
        return cutoff, at, note_id
    except (ValueError, TypeError, AttributeError):
        raise invalid_cursor() from None


def version_position(token, scope):
    data = decode_cursor(token, {"v", "scope", "cutoff", "before"}, scope)
    if type(data["cutoff"]) is not int or type(data["before"]) is not int or not 1 <= data["before"] <= data["cutoff"] <= 2_147_483_647:
        raise invalid_cursor()
    return data["cutoff"], data["before"]
