"""Current-local-day durable reminders on isolated PostgreSQL; no external delivery."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import func, select
from app.modules.jobs import service as jobs_service
from app.modules.jobs.service import deliver_outbox
from app.modules.learning import reminder_service as service
from app.modules.learning.reminder_models import DailyDigest, LearningSettings
from app.modules.learning.review_models import ReviewPlan
from app.modules.notes.models import Note
from app.shared.db import SessionLocal
from app.shared.models import Job, Notification, Outbox, User
from conftest import login, mutate
from test_reviews import create_note_plan

NOW = datetime(2026, 11, 1, 13, tzinfo=timezone.utc)


def prepare(client, now=NOW, zone="Asia/Shanghai", clock="20:00", **extra):
    login(client)
    note, plan, _ = create_note_plan(client)
    with SessionLocal.begin() as db:
        db.get(ReviewPlan, UUID(plan['id'])).next_due_at = now - timedelta(days=10)
    settings = client.get('/api/v1/learning/settings').json()
    response = mutate(client, 'PATCH', '/learning/settings', dict(revision=settings['revision'],
        timezone=zone, review_time=clock, **extra))
    assert response.status_code == 200, response.text
    return note, plan, response.json()


def count(model):
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_settings_defaults_validation_and_optimistic_conflict(client):
    login(client)
    row = client.get('/api/v1/learning/settings').json()
    assert row['timezone'] == 'Asia/Shanghai' and row['review_time'] == '20:00'
    assert row['reminder_enabled'] is True
    assert count(DailyDigest) == count(Job) == 0
    body = dict(revision=row['revision'], timezone='America/New_York', review_time='08:15', reminder_enabled=False)
    changed = mutate(client, 'PATCH', '/learning/settings', body)
    assert changed.status_code == 200 and changed.json()['revision'] == row['revision']+1
    assert mutate(client, 'PATCH', '/learning/settings', body).status_code == 409
    for field, value in [('timezone', 'Mars/Foo'), ('review_time', '8:00'), ('reminder_enabled', None)]:
        assert mutate(client, 'PATCH', '/learning/settings', dict(revision=2, **{field:value})).status_code == 422
    assert client.get('/api/v1/learning/settings').json() == changed.json()


def test_daily_digest_transaction_outbox_and_restart_deduplication(client, monkeypatch):
    monkeypatch.setattr(jobs_service, 'utcnow', lambda: NOW)
    prepare(client)
    assert service.dispatch(NOW) == 1
    assert count(DailyDigest) == count(Job) == count(Outbox) == 1
    assert count(Notification) == 0
    with SessionLocal() as db:
        job = db.scalar(select(Job))
        assert job.kind == 'learning.reminder' and job.status == 'succeeded'
        assert job.attempts == job.max_attempts == 1
    assert deliver_outbox() == 1 and deliver_outbox() == 0
    assert count(Notification) == 1
    notice = client.get('/api/v1/notifications').json()[0]
    assert notice['target']=='reviews'
    marked = mutate(client,'POST','/notifications/'+notice['id']+'/read',{})
    assert marked.status_code==200 and marked.json()['target']=='reviews' and marked.json()['read_at']
    assert service.dispatch(NOW + timedelta(hours=1)) == 0
    assert service.dispatch(NOW) == 0
    rows = client.get('/api/v1/learning/reminder-history').json()
    assert rows['total'] == 1 and rows['items'][0]['local_date'] == '2026-11-01'
    assert rows['items'][0]['counts'] == dict(note=1, card=0, quiz=0)
    assert 'job_id' not in rows['items'][0]


def test_two_workers_produce_one_digest_and_one_notification(client, monkeypatch):
    monkeypatch.setattr(jobs_service, 'utcnow', lambda: NOW)
    prepare(client)
    barrier = Barrier(2)
    def run():
        barrier.wait()
        return service.dispatch(NOW)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _: run(), range(2))) == 1
    deliver_outbox()
    assert count(DailyDigest) == count(Job) == count(Outbox) == count(Notification) == 1


def test_only_today_catchup_and_time_change_never_repeats(client):
    _, _, prefs = prepare(client)
    assert service.dispatch(NOW - timedelta(hours=2)) == 0  # 19:00 local
    assert service.dispatch(NOW + timedelta(days=4)) == 1
    first = client.get('/api/v1/learning/reminder-history').json()['items']
    assert len(first) == 1 and first[0]['local_date'] == '2026-11-05'
    assert mutate(client, 'PATCH', '/learning/settings', dict(revision=prefs['revision'], review_time='00:00')).status_code == 200
    assert service.dispatch(NOW + timedelta(days=4, hours=1)) == 0
    assert service.dispatch(NOW + timedelta(days=5)) == 1
    assert count(DailyDigest) == 2


@pytest.mark.parametrize('state', ['disabled', 'paused', 'future', 'recycled', 'user_disabled', 'none'])
def test_ineligible_or_disabled_has_no_reminder(client, state):
    note, plan, _ = prepare(client, reminder_enabled=state != 'disabled')
    with SessionLocal.begin() as db:
        row = db.get(ReviewPlan, UUID(plan['id']))
        if state == 'paused':
            row.status = 'paused'
        elif state == 'future':
            row.next_due_at = NOW+timedelta(days=1)
        elif state == 'recycled':
            db.get(Note, UUID(note['id'])).deleted_at = NOW
        elif state == 'user_disabled':
            db.scalar(select(User).where(User.username == 'admin')).status = 'disabled'
        elif state == 'none':
            row.status = 'archived'
    assert service.dispatch(NOW) == 0
    assert count(DailyDigest) == count(Job) == count(Outbox) == count(Notification) == 0


@pytest.mark.parametrize(('before', 'after', 'clock', 'day'), [
    # Spring-forward skips 02:30. First later local clock sends today only.
    ('2026-03-08T06:59:00+00:00', '2026-03-08T07:01:00+00:00', '02:30', '2026-03-08'),
    # Repeated autumn hour must never send twice for one local date.
    ('2026-11-01T05:30:00+00:00', '2026-11-01T06:30:00+00:00', '01:30', '2026-11-01'),
])
def test_dst_gap_and_fold(client, before, after, clock, day):
    first, second = datetime.fromisoformat(before), datetime.fromisoformat(after)
    prepare(client, now=first, zone='America/New_York', clock=clock)
    early = service.dispatch(first)
    later = service.dispatch(second)
    assert early+later == 1
    assert service.dispatch(second + timedelta(minutes=1)) == 0
    row = client.get('/api/v1/learning/reminder-history').json()['items'][0]
    assert row['local_date'] == day and row['timezone'] == 'America/New_York'


def test_local_midnight_and_extreme_zone(client):
    first = datetime(2026, 11, 1, 9, 59, tzinfo=timezone.utc)
    prepare(client, now=first, zone='Pacific/Kiritimati', clock='00:00')
    assert service.dispatch(first) == 1
    assert service.dispatch(first + timedelta(minutes=1)) == 1
    dates = {r['local_date'] for r in client.get('/api/v1/learning/reminder-history').json()['items']}
    assert dates == {'2026-11-01','2026-11-02'}


def test_preferences_and_history_are_private(client):
    prepare(client)
    assert service.dispatch(NOW) == 1
    from app.shared.security import hasher
    with SessionLocal.begin() as db:
        db.add(User(username='another', display_name='另一个账号', password_hash=hasher.hash('another-password'), role='user'))
    mutate(client, 'POST', '/auth/logout', {})
    login(client, 'another', 'another-password')
    assert client.get('/api/v1/learning/reminder-history').json()['total'] == 0
    own = client.get('/api/v1/learning/settings').json()
    assert own['revision'] == 1
    assert service.dispatch(NOW) == 0
    assert count(DailyDigest) == 1


def test_dispatch_rejects_naive_clock_or_unbounded_batch():
    for args in [dict(now=datetime(2026,1,1)), dict(now=NOW, limit=0), dict(now=NOW, limit=1001), dict(now=NOW, limit=True)]:
        with pytest.raises(ValueError):
            service.dispatch(**args)


def test_delivery_same_day_delay_is_sent_once(client, monkeypatch):
    prepare(client)
    assert service.dispatch(NOW) == 1
    later = NOW + timedelta(hours=1)
    # A batch uses one instant for date admission and both persisted timestamps.
    calls = []
    def clock():
        calls.append(True)
        return later
    monkeypatch.setattr(jobs_service, 'utcnow', clock)
    assert deliver_outbox() == 1
    assert len(calls) == 1
    with SessionLocal() as db:
        notice, event = db.scalar(select(Notification)), db.scalar(select(Outbox))
        assert notice.event_id == event.id
        assert notice.created_at == event.delivered_at == later
    assert deliver_outbox() == 0
    assert count(Notification) == 1


def test_delivery_restart_expires_pending_digest_but_preserves_history_and_other_events(client, monkeypatch):
    previous = NOW - timedelta(days=1)
    prepare(client, now=previous)
    assert service.dispatch(previous) == 1
    monkeypatch.setattr(jobs_service, 'utcnow', lambda: previous)
    assert deliver_outbox() == 1
    with SessionLocal() as db:
        history_notice = db.scalar(select(Notification))
        history_id, history_created = history_notice.id, history_notice.created_at

    assert service.dispatch(NOW) == 1  # Transaction committed; worker dies before delivery.
    with SessionLocal() as db:
        pending = db.scalar(select(Outbox).where(Outbox.delivered_at.is_(None)))
        pending_id = pending.id
    monkeypatch.setattr(jobs_service, 'utcnow', lambda: NOW)
    response = mutate(client, 'POST', '/jobs/diagnostic', dict(request_key='delayed-diagnostic', duration_seconds=0))
    assert response.status_code == 202, response.text
    job = jobs_service.claim()
    assert job and jobs_service.finish(job['id'], job['token'], {'ok': True})

    restarted = NOW + timedelta(days=2)
    monkeypatch.setattr(jobs_service, 'utcnow', lambda: restarted)
    assert deliver_outbox() == 1  # Only the ordinary diagnostic event delivers.
    assert deliver_outbox() == 0
    assert count(DailyDigest) == 2 and count(Job) == count(Outbox) == 3
    assert count(Notification) == 2
    with SessionLocal() as db:
        assert db.get(Outbox, pending_id).delivered_at == restarted
        assert db.scalar(select(Notification).where(Notification.event_id == pending_id)) is None
        history_notice = db.get(Notification, history_id)
        assert history_notice.created_at == history_created
        diagnostic = db.scalar(select(Notification).join(Outbox, Outbox.id == Notification.event_id)
                               .where(Outbox.kind == 'job.succeeded'))
        assert diagnostic and diagnostic.created_at == restarted
    history = client.get('/api/v1/learning/reminder-history').json()
    assert history['total'] == 2
    assert {row['local_date'] for row in history['items']} == {'2026-10-31', '2026-11-01'}


@pytest.mark.parametrize(('zone', 'first', 'later', 'changed_zone', 'expected'), [
    # UTC stays November 1, but the ledger's UTC+14 zone has entered November 2.
    ('Pacific/Kiritimati', '2026-11-01T09:59:00+00:00', '2026-11-01T10:00:00+00:00', 'America/Los_Angeles', 0),
    # UTC enters November 2, while the ledger's zone remains November 1.
    ('America/Los_Angeles', '2026-11-01T23:59:00+00:00', '2026-11-02T00:01:00+00:00', 'Pacific/Kiritimati', 1),
])
def test_delivery_uses_ledger_local_date_even_after_preference_change(client, monkeypatch, zone, first, later, changed_zone, expected):
    first, later = datetime.fromisoformat(first), datetime.fromisoformat(later)
    _, _, prefs = prepare(client, now=first, zone=zone, clock='00:00')
    assert service.dispatch(first) == 1
    assert mutate(client, 'PATCH', '/learning/settings', dict(revision=prefs['revision'], timezone=changed_zone)).status_code == 200
    monkeypatch.setattr(jobs_service, 'utcnow', lambda: later)
    assert deliver_outbox() == expected
    assert deliver_outbox() == 0
    assert count(Notification) == expected and count(DailyDigest) == 1
    with SessionLocal() as db:
        assert db.scalar(select(DailyDigest)).timezone == zone
        assert db.scalar(select(Outbox)).delivered_at == later


@pytest.mark.parametrize('damage', ['missing_ledger', 'different_owner', 'unknown_timezone', 'invalid_timezone'])
def test_delivery_quarantines_unattributable_digest_without_blocking_other_events(client, monkeypatch, damage):
    prepare(client)
    assert service.dispatch(NOW) == 1
    monkeypatch.setattr(jobs_service, 'utcnow', lambda: NOW)
    with SessionLocal.begin() as db:
        digest = db.scalar(select(DailyDigest))
        if damage == 'missing_ledger':
            db.delete(digest)
        elif damage == 'different_owner':
            other = User(username='digest-owner', display_name='合成账号', role='user', password_hash='unused-test-hash')
            db.add(other)
            db.flush()
            digest.owner_id = other.id
        else:
            digest.timezone = 'Mars/Foo' if damage == 'unknown_timezone' else '../invalid'
    response = mutate(client, 'POST', '/jobs/diagnostic', dict(request_key='healthy-diagnostic', duration_seconds=0))
    assert response.status_code == 202, response.text
    job = jobs_service.claim()
    assert job and jobs_service.finish(job['id'], job['token'], {'ok': True})
    assert deliver_outbox() == 1
    assert deliver_outbox() == 0
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.delivered_at.is_(None))) == 0
        notices = db.scalars(select(Notification).join(Outbox, Outbox.id == Notification.event_id)).all()
        assert len(notices) == 1 and notices[0].message == '后台自检已完成'
