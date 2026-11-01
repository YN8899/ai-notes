"""Local-date in-app reminders; no emails, webhooks, model calls or timers in UI."""
from datetime import timezone
from uuid import uuid4
from zoneinfo import ZoneInfo
from sqlalchemy import func, select
from app.modules.learning.reminder_models import DailyDigest, LearningSettings
from app.modules.learning.review_models import ReviewItem, ReviewPlan
from app.modules.learning.review_service import eligible_clause, lock_owner
from app.shared.db import SessionLocal, utcnow
from app.shared.errors import AppError
from app.shared.models import Job, Outbox, User


def preference(db, owner, *, lock=False):
    # Mutations and the worker share owner -> preference lock order. A first GET
    # persists defaults but cannot send a reminder or call an external service.
    lock_owner(db, owner)
    statement = select(LearningSettings).where(LearningSettings.owner_id == owner)
    if lock:
        statement = statement.with_for_update()
    row = db.scalar(statement.execution_options(populate_existing=True))
    if row is None:
        row = LearningSettings(owner_id=owner)
        db.add(row)
        db.flush()
    return row


def get_settings(db, owner):
    row = preference(db, owner)
    db.commit()
    return row


def patch_settings(db, owner, data):
    row = preference(db, owner, lock=True)
    if row.revision != data.revision:
        raise AppError(409, "revision_conflict", "偏好已在其他页面更新，请保留输入并重新读取")
    for key in data.model_fields_set - {"revision"}:
        setattr(row, key, getattr(data, key))
    row.revision += 1
    row.updated_at, row.last_checked_at = utcnow(), None
    db.commit()
    return row


def due_counts(db, owner, now):
    values = db.execute(select(ReviewItem.kind, func.count(ReviewPlan.id)).join(
        ReviewPlan, ReviewPlan.item_id == ReviewItem.id).where(
            ReviewPlan.owner_id == owner, ReviewItem.owner_id == owner,
            ReviewPlan.status == "active", ReviewPlan.next_due_at <= now,
            eligible_clause()).group_by(ReviewItem.kind))
    return {"note": 0, "card": 0, "quiz": 0, **dict(values.all())}


def history(db, owner, offset=0, limit=50):
    condition = DailyDigest.owner_id == owner
    total = db.scalar(select(func.count()).select_from(DailyDigest).where(condition))
    rows = list(db.scalars(select(DailyDigest).where(condition).order_by(
        DailyDigest.local_date.desc(), DailyDigest.id.desc()).offset(offset).limit(limit)))
    return dict(items=rows, total=total, offset=offset, limit=limit)


def dispatch(now=None, limit=100):
    """Process a fair bounded batch. Crashes commit everything or nothing per user.

    Every loop considers only the current local date, not missed historical days.
    A unique ledger plus the existing outbox protects multi-worker/restart paths.
    """
    now = now or utcnow()
    if now.tzinfo is None or now.utcoffset() is None or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("invalid_reminder_scan")
    now = now.astimezone(timezone.utc)
    # Seed defaults lazily for active accounts that have never opened settings.
    with SessionLocal() as db:
        new_ids = list(db.scalars(select(User.id).where(User.status == "active", ~select(
            LearningSettings.id).where(LearningSettings.owner_id == User.id).exists())
            .order_by(User.id).limit(limit)))
    for owner in new_ids:
        with SessionLocal.begin() as db:
            user = db.scalar(select(User).where(User.id == owner, User.status == "active")
                             .with_for_update(skip_locked=True).execution_options(populate_existing=True))
            if user:
                preference(db, owner)
    with SessionLocal() as db:
        ids = list(db.scalars(select(LearningSettings.owner_id).join(User, User.id == LearningSettings.owner_id)
            .where(User.status == "active")
            .order_by(LearningSettings.last_checked_at.asc().nulls_first(), LearningSettings.owner_id).limit(limit)))
    sent = 0
    for owner in ids:
        with SessionLocal.begin() as db:
            user = db.scalar(select(User).where(User.id == owner, User.status == "active")
                             .with_for_update(skip_locked=True).execution_options(populate_existing=True))
            if not user:
                continue
            setting = preference(db, owner, lock=True)
            setting.last_checked_at = now
            if not setting.reminder_enabled:
                continue
            local = now.astimezone(ZoneInfo(setting.timezone))
            hour, minute = map(int, setting.review_time.split(":"))
            if (local.hour, local.minute) < (hour, minute):
                continue
            existing = db.scalar(select(DailyDigest.id).where(DailyDigest.owner_id == owner,
                DailyDigest.kind == "daily_review", DailyDigest.local_date == local.date()))
            if existing:
                continue
            counts = due_counts(db, owner, now)
            total = sum(counts.values())
            if not total:
                continue
            message = f"今日待复习 {total} 项：笔记 {counts['note']}、卡片 {counts['card']}、测验 {counts['quiz']}。到复习中心查看。"
            # The ledger owns deduplication. A random internal Job key cannot
            # collide with a member's unrelated diagnostic request namespace.
            job = Job(owner_id=owner, kind="learning.reminder", dedupe_key="review-digest:"+str(uuid4()),
                payload={}, status="succeeded", attempts=1, max_attempts=1,
                run_after=now, result={"local_date": local.date().isoformat(), "counts": counts})
            db.add(job)
            db.flush()
            db.add(DailyDigest(owner_id=owner, local_date=local.date(), timezone=setting.timezone,
                counts=counts, job_id=job.id))
            db.add(Outbox(user_id=owner, job_id=job.id, kind="review.daily",
                          payload={"message": message}))
            sent += 1
    return sent
