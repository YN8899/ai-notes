"""Durable notification feed. NOTIFY is only a best-effort wakeup hint."""
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from app.shared.db import utcnow
from app.shared.models import Notification, Outbox
from app.modules.jobs.schemas import NotificationOut
from app.modules.social.realtime_signal import publish
from .feed_models import NotificationCounter


def counter(db, owner_id):
    db.execute(insert(NotificationCounter).values(owner_id=owner_id, head_seq=0, read_revision=0)
        .on_conflict_do_nothing(index_elements=[NotificationCounter.owner_id]))
    return db.scalar(select(NotificationCounter).where(NotificationCounter.owner_id == owner_id)
        .with_for_update().execution_options(populate_existing=True))


def persist(db, event, *, now=None):
    """Lock the owner counter until commit: another commit cannot overtake seq."""
    state = counter(db, event.user_id)
    old = db.scalar(select(Notification).where(Notification.event_id == event.id))
    if old:
        return old
    state.head_seq += 1
    notice = Notification(id=uuid4(), user_id=event.user_id, event_id=event.id,
        seq=state.head_seq, message=event.payload["message"], created_at=now or utcnow())
    db.add(notice)
    db.flush()
    publish(db, event.user_id, "notifications")
    return notice


def read(db, owner_id, notice_id):
    # Same owner-counter -> notification lock order as persistence.
    state = counter(db, owner_id)
    row = db.scalar(select(Notification).where(Notification.id == notice_id,
        Notification.user_id == owner_id).with_for_update())
    if row is None:
        from app.shared.errors import AppError
        raise AppError(404, "not_found", "通知不存在")
    if row.read_at is None:
        row.read_at = utcnow()
        state.read_revision += 1
        publish(db, owner_id, "notifications")
    return row


def page(db, owner_id, after_seq, limit):
    state = db.get(NotificationCounter, owner_id)
    # Capture head before selecting rows so concurrent inserts cannot make a
    # page claim progress beyond its declared durable snapshot.
    head, revision = (state.head_seq, state.read_revision) if state else (0, 0)
    rows = db.execute(select(Notification, Outbox.kind).join(Outbox, Outbox.id == Notification.event_id)
        .where(Notification.user_id == owner_id, Outbox.user_id == owner_id,
            Notification.seq > after_seq, Notification.seq <= head)
        .order_by(Notification.seq).limit(limit + 1)).all()
    visible = rows[:limit]
    return dict(items=[NotificationOut.model_validate(row).model_copy(update={
        "target": "reviews" if kind == "review.daily" else None}) for row, kind in visible],
        next_after_seq=visible[-1][0].seq if visible else after_seq,
        has_more=len(rows) > limit, head_seq=head, read_revision=revision)
