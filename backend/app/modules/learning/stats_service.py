"""Recompute descriptive facts; never infer mastery or an FSRS rating from time/scores."""
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
from zoneinfo import ZoneInfo
from sqlalchemy import func, select
from app.modules.learning import stats_schemas as s
from app.modules.learning.event_models import LearningEvent
from app.modules.learning.quiz_models import QuizAttempt, QuizAnswer
from app.modules.learning.reminder_models import LearningSettings
from app.modules.learning.review_models import ReviewItem, ReviewLog, ReviewPlan
from app.modules.learning.review_service import eligible_clause, lock_owner, source_resource
from app.modules.notes.models import Note
from app.shared.db import utcnow
from app.shared.errors import AppError

SCAN_CAP = 50000
DAILY_EVENT_CAP = 10000
KINDS = ('note', 'card', 'quiz')


def record(db, owner, data):
    # The shared owner lock serializes duplicate IDs, quotas and other learning
    # mutations. A failed batch has no commit; dependency teardown rolls it back.
    lock_owner(db, owner)
    now = utcnow()
    known = {row.event_id: row.request_hash for row in db.scalars(select(LearningEvent).where(
        LearningEvent.owner_id == owner, LearningEvent.event_id.in_([e.event_id for e in data.events])))}
    accepted = duplicates = 0
    quotas = {}
    for event in data.events:
        digest = hashlib.sha256(json.dumps(event.model_dump(mode='json'), sort_keys=True,
                               separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        if event.event_id in known:
            if known[event.event_id] != digest:
                raise AppError(409, 'event_id_conflict', '相同活动标识已用于不同内容')
            duplicates += 1
            continue
        if event.ended_at > now or event.started_at < now - timedelta(hours=24):
            raise AppError(422, 'event_time_invalid', '活动必须在过去24小时内且不能包含未来时间')
        resource_kind = next(kind for kind in KINDS if getattr(event, kind+'_id') is not None)
        _, eligible = source_resource(db, owner, resource_kind, getattr(event, resource_kind+'_id'))
        if not eligible:
            raise AppError(409, 'activity_source_unavailable', '内容已失效，请停止计时并刷新')
        # A fixed UTC-day quota is stable even if the user changes preferences.
        day = event.started_at.date()
        if day not in quotas:
            start = datetime.combine(day, time.min, timezone.utc)
            quotas[day] = db.scalar(select(func.count()).select_from(LearningEvent).where(
                LearningEvent.owner_id == owner, LearningEvent.started_at >= start,
                LearningEvent.started_at < start + timedelta(days=1)))
        if quotas[day] >= DAILY_EVENT_CAP:
            raise AppError(429, 'activity_daily_limit', '当天活动记录已达上限')
        quotas[day] += 1
        db.add(LearningEvent(owner_id=owner, request_hash=digest, **event.model_dump()))
        known[event.event_id] = digest
        accepted += 1
    db.commit()
    return s.ActivityBatchOut(accepted=accepted, duplicates=duplicates)


def window(db, owner, date_from, date_to):
    name = db.scalar(select(LearningSettings.timezone).where(LearningSettings.owner_id == owner)) or 'Asia/Shanghai'
    zone = ZoneInfo(name)
    today = utcnow().astimezone(zone).date()
    date_to = date_to or today
    # Validate supplied endpoints before subtracting the default 29-day window:
    # date.min cannot support that arithmetic and must produce a controlled 422.
    if not 1900 <= date_to.year < 9999 or date_from is not None and not 1900 <= date_from.year < 9999:
        raise AppError(422, 'statistics_range_invalid', '日期范围须为1900年至9998年内连续1至93天')
    date_from = date_from or date_to - timedelta(days=29)
    if date_to < date_from or (date_to-date_from).days >= 93 or date_from.year < 1900 or date_to.year >= 9999:
        raise AppError(422, 'statistics_range_invalid', '日期范围须为1900年至9998年内连续1至93天')
    days = [date_from + timedelta(days=i) for i in range((date_to-date_from).days+1)]
    # Construct each local midnight separately: adjacent UTC boundaries may be
    # 23/25 hours apart on DST days. Never add 24h to an earlier UTC midnight.
    bounds = [(datetime.combine(day, time.min, zone).astimezone(timezone.utc),
               datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(timezone.utc)) for day in days]
    return name, zone, days, bounds


def bounded(db, query):
    rows = list(db.execute(query.limit(SCAN_CAP+1)))
    if len(rows) > SCAN_CAP:
        raise AppError(422, 'statistics_scan_limit', '范围内记录过多，请缩短日期范围')
    return rows


def merged_seconds(intervals):
    end = None
    seconds = 0.0
    for left, right in sorted(intervals):
        if end is None or left > end:
            seconds += (right-left).total_seconds()
            end = right
        elif right > end:
            seconds += (right-end).total_seconds()
            end = right
    return seconds


def history(db, owner, start, end, kind=None):
    query = select(ReviewLog, ReviewItem.kind).join(ReviewItem,
        (ReviewItem.id == ReviewLog.item_id) & (ReviewItem.owner_id == ReviewLog.owner_id)).where(
        ReviewLog.owner_id == owner, ReviewLog.reviewed_at >= start, ReviewLog.reviewed_at < end,
        ReviewLog.action.in_(['review', 'practice']))
    if kind:
        query = query.where(ReviewItem.kind == kind)
    return bounded(db, query)


def quiz_facts(db, owner, start, end, zone):
    attempts = bounded(db, select(QuizAttempt).where(QuizAttempt.owner_id == owner,
        QuizAttempt.submitted_at >= start, QuizAttempt.submitted_at < end))
    answers = bounded(db, select(QuizAnswer).join(QuizAttempt,
        (QuizAttempt.id == QuizAnswer.attempt_id) & (QuizAttempt.owner_id == QuizAnswer.owner_id)).where(
        QuizAnswer.owner_id == owner, QuizAttempt.submitted_at >= start, QuizAttempt.submitted_at < end))
    by_attempt = {}
    for answer, in answers:
        by_attempt.setdefault(answer.attempt_id, []).append(answer)
    result = {}
    for attempt, in attempts:
        fact = result.setdefault(attempt.submitted_at.astimezone(zone).date(), s.QuizStats())
        current = by_attempt.get(attempt.id, [])
        fact.submitted += 1
        # Answers are the mutable grading authority. A human correction is
        # reflected immediately without relying on a cached attempt total.
        needs_review = any(a.status == 'needs_review' for a in current)
        graded = len(current) == len(attempt.snapshot) and all(a.status == 'graded' for a in current)
        fact.needs_review += int(needs_review)
        fact.graded += int(graded)
        fact.actual_points += sum(a.points or 0 for a in current)
        fact.max_points += attempt.max_points
        fact.manually_corrected += sum(a.provenance == 'manual' for a in current)
    return result


def statistics(db, owner, date_from=None, date_to=None):
    name, zone, days, bounds = window(db, owner, date_from, date_to)
    start, end = bounds[0][0], bounds[-1][1]
    events = bounded(db, select(LearningEvent).where(LearningEvent.owner_id == owner,
        LearningEvent.started_at >= start-timedelta(seconds=60),
        LearningEvent.started_at < end, LearningEvent.ended_at > start))
    rows = {day: s.StatsDay(date=day) for day in days}
    reading_notes = {day: set() for day in days}
    recorded_notes = set()
    intervals = {day: {'all': [], 'reading': [], 'review': [], 'card_practice': [], 'quiz': []} for day in days}
    for event, in events:
        # Events span <=60s, so normally one day, at most two adjacent days.
        for day, (left, right) in zip(days, bounds):
            a, b = max(left, event.started_at), min(right, event.ended_at)
            if b > a:
                intervals[day]['all'].append((a,b))
                intervals[day][event.kind].append((a,b))
                if event.kind == 'reading':
                    reading_notes[day].add(event.note_id)
                    recorded_notes.add(event.note_id)
    names = {'all':'total_seconds', 'reading':'reading_seconds', 'review':'review_seconds',
             'card_practice':'practice_seconds', 'quiz':'quiz_seconds'}
    for day in days:
        rows[day].reading_notes = len(reading_notes[day])
        for kind, attr in names.items():
            setattr(rows[day], attr, merged_seconds(intervals[day][kind]))
    objects = {day: set() for day in days}
    for log, _ in history(db, owner, start, end):
        day = log.reviewed_at.astimezone(zone).date()
        row = rows[day]
        if log.action == 'review':
            row.formal_reviews += 1
            objects[day].add(log.item_id)
        else:
            row.practice_count += 1
        key = ('again','hard','good','easy')[log.rating-1]
        setattr(row.ratings, key, getattr(row.ratings, key)+1)
    for day in days:
        rows[day].distinct_review_objects = len(objects[day])
    for day, quiz in quiz_facts(db, owner, start, end, zone).items():
        rows[day].quiz = quiz
    # IDs come only from the bounded owner-scoped event query above. Count the
    # active numerator and denominator in one SQL snapshot, without loading all
    # notes or summing daily distinct counts (the same note can span many days).
    active_total, active_read = db.execute(select(func.count(Note.id),
        func.count(Note.id).filter(Note.id.in_(recorded_notes))).where(
            Note.owner_id == owner, Note.deleted_at.is_(None))).one()
    return s.StatsOut(timezone=name, date_from=days[0], date_to=days[-1], days=list(rows.values()),
        reading_progress=s.ReadingProgress(recorded_notes=len(recorded_notes),
            active_notes_read=active_read, active_notes_total=active_total))


def calendar(db, owner, date_from=None, date_to=None, kind=None):
    name, zone, days, bounds = window(db, owner, date_from, date_to)
    start, end = bounds[0][0], bounds[-1][1]
    rows = {day: s.CalendarDay(date=day) for day in days}
    for log, resource_kind in history(db, owner, start, end, kind):
        row = rows[log.reviewed_at.astimezone(zone).date()]
        field = row.completed if log.action == 'review' else row.practice
        setattr(field, resource_kind, getattr(field, resource_kind)+1)
    if kind in (None, 'quiz'):
        for day, quiz in quiz_facts(db, owner, start, end, zone).items():
            rows[day].quiz = quiz
    query = select(ReviewPlan.next_due_at, ReviewItem.kind).join(ReviewItem,
        (ReviewItem.id == ReviewPlan.item_id) & (ReviewItem.owner_id == ReviewPlan.owner_id)).where(
        ReviewPlan.owner_id == owner, ReviewPlan.status == 'active', eligible_clause(),
        ReviewPlan.next_due_at >= max(start, utcnow()), ReviewPlan.next_due_at < end)
    if kind:
        query = query.where(ReviewItem.kind == kind)
    for due, resource_kind in bounded(db, query):
        row = rows[due.astimezone(zone).date()].due
        setattr(row, resource_kind, getattr(row, resource_kind)+1)
    return s.CalendarOut(timezone=name, date_from=days[0], date_to=days[-1], kind=kind, days=list(rows.values()))
