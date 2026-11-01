"""Versioned pure scheduling adapter; persistence/idempotence belongs to reviews.

Uses the official library, with deterministic intervals for reproducible logs.
Never derives a memory rating from a quiz score or a passive reading event.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math

from fsrs import Card, Rating, Scheduler

from app.shared.errors import AppError

FSRS_VERSION = "py-fsrs/6.3.2"
MANUAL_VERSION = "manual/1"
_CARD_KEYS = {"card_id", "state", "step", "stability", "difficulty", "due", "last_review"}


@dataclass(frozen=True)
class Scheduled:
    next_due_at: datetime
    scheduler_state: dict
    algorithm_version: str


def _error():
    return AppError(409, "scheduler_state_invalid", "排期状态不兼容或已损坏，请核对计划后重试")


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AppError(422, "schedule_time_invalid", "排期时间必须包含时区")
    return value.astimezone(timezone.utc)


def _scheduler():
    # Do not import optional optimizer dependencies. No user-supplied weights.
    return Scheduler(enable_fuzzing=False)


def initialize(strategy, now, *, card_id=0):
    now = utc(now)
    if strategy == "manual":
        return Scheduled(now, {}, MANUAL_VERSION)
    if strategy != "fsrs" or type(card_id) is not int or not 0 <= card_id < 2**63:
        raise AppError(422, "schedule_strategy_invalid", "排期策略或标识无效")
    return Scheduled(now, {"card": Card(card_id=card_id, due=now).to_dict()}, FSRS_VERSION)


def _card(state, now):
    try:
        if not isinstance(state, dict) or set(state) != {"card"}:
            raise ValueError()
        record = state["card"]
        if not isinstance(record, dict) or set(record) != _CARD_KEYS:
            raise ValueError()
        if type(record["card_id"]) is not int or not 0 <= record["card_id"] < 2**63:
            raise ValueError()
        if type(record["state"]) is not int or record["state"] not in {1, 2, 3}:
            raise ValueError()
        step = record["step"]
        if record["state"] == 2:
            if step is not None:
                raise ValueError()
        elif type(step) is not int or not 0 <= step <= 32:
            raise ValueError()
        for field, maximum in (("stability", 1e9), ("difficulty", 10)):
            value = record[field]
            if value is not None and (type(value) not in {int, float} or not math.isfinite(value)
                                      or not 0 < value <= maximum):
                raise ValueError()
        # Official Review/Relearning branches require learned memory values.
        # A half-initialized pair is invalid even for a Learning card.
        if ((record["stability"] is None) != (record["difficulty"] is None)
                or record["state"] in {2, 3} and record["stability"] is None
                or (record["stability"] is None) != (record["last_review"] is None)):
            raise ValueError()
        for field in ("due", "last_review"):
            value = record[field]
            if value is None and field == "last_review":
                continue
            if not isinstance(value, str):
                raise ValueError()
            parsed = utc(datetime.fromisoformat(value))
            if field == "last_review" and parsed > now:
                raise ValueError()
        return Card.from_dict(deepcopy(record))
    except (ValueError, TypeError, KeyError, OverflowError, AppError):
        raise _error() from None


def schedule(strategy, state, rating, now, *, algorithm_version=None, manual_due=None):
    """Return a new state; callers lock/check plan revision and append one log.

    Temporary practice must not call this function. It gets a practice log only.
    """
    now = utc(now)
    if type(rating) is not int or rating not in {1, 2, 3, 4}:
        raise AppError(422, "review_rating_invalid", "请选择忘记、困难、良好或轻松")
    if strategy == "manual":
        # Historical FSRS state belongs to ReviewPlan.last_fsrs_state. A manual
        # adapter state has no schema beyond an empty object; never carry or
        # silently normalize unknown state through a successful review.
        if algorithm_version != MANUAL_VERSION or type(state) is not dict or state != {}:
            raise _error()
        if manual_due is None:
            raise AppError(422, "manual_due_required", "手动排期需要指定下次复习时间")
        due = utc(manual_due)
        if not now < due <= now + timedelta(days=36500):
            raise AppError(422, "schedule_time_invalid", "下次复习时间须晚于现在，且不超过100年")
        return Scheduled(due, {}, MANUAL_VERSION)
    if strategy != "fsrs":
        raise AppError(422, "schedule_strategy_invalid", "排期策略无效")
    if algorithm_version not in {None, FSRS_VERSION}:
        raise _error()
    card = _card(state, now)
    try:
        updated, _ = _scheduler().review_card(card, Rating(rating), review_datetime=now)
    except (ValueError, TypeError, OverflowError, ZeroDivisionError, AssertionError):
        raise _error() from None
    return Scheduled(utc(updated.due), {"card": updated.to_dict()}, FSRS_VERSION)
