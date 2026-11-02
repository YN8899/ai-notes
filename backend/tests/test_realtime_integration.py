"""Integration-only: copy into backend/tests after merge; disposable PG required.

Uses the project's existing destructive isolated-database fixture. These are
SQL/transaction tests, never described as two real API-process coverage.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4
from threading import Event
import psycopg
from sqlalchemy import func, select
from app.shared.db import engine, SessionLocal, utcnow
from app.shared.models import User, Job, Outbox, Notification
from app.modules.jobs import service
from app.modules.notifications import feed
from app.modules.notifications.feed_models import NotificationCounter
from app.modules.social.realtime_signal import CHANNEL, publish, decode


def owner():
    with SessionLocal() as db:
        return db.scalar(select(User.id).where(User.username == "admin"))


def event(user_id):
    with SessionLocal.begin() as db:
        row = Job(owner_id=user_id, kind="diagnostic", status="succeeded", dedupe_key=str(uuid4()),
            payload={"duration_seconds": 0}, result={"ok": True})
        db.add(row); db.flush()
        item = Outbox(user_id=user_id, job_id=row.id, kind="job.succeeded", payload={"message": "synthetic"})
        db.add(item); db.flush()
        return item.id


def test_notify_is_transactional_rollback_sends_nothing():
    url = engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(url, autocommit=True, connect_timeout=3) as listener:
        listener.execute("LISTEN " + CHANNEL)
        with SessionLocal() as db:
            publish(db, owner(), "chat")
            db.rollback()
        assert list(listener.notifies(timeout=0.15, stop_after=1)) == []
        with SessionLocal.begin() as db:
            publish(db, owner(), "notifications")
        values = list(listener.notifies(timeout=1, stop_after=1))
        assert len(values) == 1 and decode(values[0].payload) == (owner(), "notifications")


def test_durable_275_backlog_owner_isolation_and_read_revision():
    user_id = owner()
    for _ in range(275):
        event(user_id)
    while service.deliver_outbox():
        pass
    cursor, ids = 0, []
    with SessionLocal() as db:
        while True:
            result = feed.page(db, user_id, cursor, 100)
            assert len(result["items"]) <= 100
            ids.extend(row.id for row in result["items"])
            cursor = result["next_after_seq"]
            if not result["has_more"]:
                break
        assert cursor == result["head_seq"] == 275 and len(set(ids)) == 275
        assert feed.page(db, uuid4(), 0, 100)["items"] == []
    with SessionLocal.begin() as db:
        feed.read(db, user_id, ids[0])
    with SessionLocal.begin() as db:
        feed.read(db, user_id, ids[0])  # idempotent read, revision increments once
    with SessionLocal() as db:
        final = feed.page(db, user_id, cursor, 100)
        assert final["items"] == [] and final["head_seq"] == 275 and final["read_revision"] == 1
        assert db.get(Notification, ids[0]).read_at is not None


def test_owner_lock_serializes_concurrent_delivery_and_rollback_does_not_advance():
    user_id = owner()
    events = [event(user_id) for _ in range(20)]
    def persist(event_id):
        with SessionLocal.begin() as db:
            item = db.get(Outbox, event_id)
            return feed.persist(db, item).seq
    with ThreadPoolExecutor(max_workers=4) as executor:
        values = list(executor.map(persist, events))
    assert sorted(values) == list(range(1, 21))
    with SessionLocal() as db:
        notice = feed.persist(db, db.get(Outbox, event(user_id)))
        assert notice.seq == 21
        db.rollback()
    with SessionLocal() as db:
        assert db.get(NotificationCounter, user_id).head_seq == 20
    # Same immutable event delivered again does not create another sequence.
    assert persist(events[0]) in values


def test_two_claimers_and_lease_recovery_fence_old_token_exactly_one_outbox():
    user_id = owner()
    with SessionLocal.begin() as db:
        row = Job(owner_id=user_id, kind="diagnostic", dedupe_key=str(uuid4()), payload={"duration_seconds": 0})
        db.add(row); db.flush(); job_id = row.id
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _: service.claim(kinds=("diagnostic",)), range(2)))
    first, = [claim for claim in claims if claim]
    with SessionLocal.begin() as db:
        db.get(Job, job_id).lease_until = utcnow() - timedelta(seconds=1)
    recovered = service.claim(kinds=("diagnostic",))
    assert recovered and recovered["token"] != first["token"]
    assert not service.heartbeat(job_id, first["token"])
    assert not service.finish(job_id, first["token"], {"ok": True})
    assert service.finish(job_id, recovered["token"], {"ok": True})
    assert not service.finish(job_id, recovered["token"], {"ok": True})
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.job_id == job_id)) == 1


def test_counter_prevents_later_seq_committing_before_earlier_transaction():
    user_id = owner()
    first_id, second_id = event(user_id), event(user_id)
    locked, release = Event(), Event()
    def first():
        with SessionLocal.begin() as db:
            notice = feed.persist(db, db.get(Outbox, first_id))
            locked.set()
            assert release.wait(3)
            return notice.seq
    def second():
        assert locked.wait(3)
        with SessionLocal.begin() as db:
            return feed.persist(db, db.get(Outbox, second_id)).seq
    with ThreadPoolExecutor(max_workers=2) as executor:
        earlier = executor.submit(first)
        assert locked.wait(3)
        later = executor.submit(second)
        try:
            # The second call has an independent DB transaction and cannot
            # finish/commit while the earlier counter lock is held.
            from concurrent.futures import TimeoutError
            try:
                later.result(timeout=0.1)
                raise AssertionError("counter_did_not_serialize")
            except TimeoutError:
                pass
        finally:
            release.set()
        assert earlier.result(timeout=3) == 1
        assert later.result(timeout=3) == 2


def test_two_outbox_workers_create_each_notification_only_once():
    user_id = owner()
    for _ in range(75):
        event(user_id)
    with ThreadPoolExecutor(max_workers=2) as executor:
        totals = list(executor.map(lambda _: service.deliver_outbox(), range(2)))
    while service.deliver_outbox():
        pass
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Notification)) == 75
        assert db.get(NotificationCounter, user_id).head_seq == 75
        assert not db.scalar(select(func.count()).select_from(Outbox).where(Outbox.delivered_at.is_(None)))


def test_data_bearing_downgrade_refuses_without_losing_cursor_facts():
    import importlib.util
    from pathlib import Path
    import pytest
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    user_id=owner();event(user_id);service.deliver_outbox()
    path=Path(__file__).parents[1]/'alembic/versions/29de03d04789_realtime_notification_feed.py'
    spec=importlib.util.spec_from_file_location('t34_migration',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with engine.connect() as connection:
        transaction=connection.begin()
        try:
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError,match='notification_feed_downgrade_has_data'):
                    module.downgrade()
        finally:
            transaction.rollback()  # RED also cannot destroy this synthetic fixture.
    with SessionLocal() as db:
        assert db.get(NotificationCounter,user_id).head_seq==1
        assert db.scalar(select(Notification.seq).where(Notification.user_id==user_id))==1


def test_restore_normalize_preserves_feed_but_revokes_ephemeral_authority():
    import importlib,sys
    from pathlib import Path
    from types import SimpleNamespace
    from app.shared.db import Base
    from app.shared.models import AuthSession
    from app.modules.notifications.models import PushSubscription
    from app.modules.social.chat_models import RealtimeTicket
    user_id=owner();event(user_id);service.deliver_outbox()
    with SessionLocal.begin() as db:
        notice=db.scalar(select(Notification).where(Notification.user_id==user_id))
        feed.read(db,user_id,notice.id)
        session=AuthSession(user_id=user_id,token_hash=uuid4().hex*2,csrf_hash=uuid4().hex*2,expires_at=utcnow()+timedelta(hours=1))
        db.add(session);db.flush();session_id=session.id
        db.add(PushSubscription(owner_id=user_id,session_id=session.id,endpoint_hash=uuid4().hex*2,
            endpoint='https://fcm.googleapis.com/t34-synthetic',p256dh='A'*87,auth='A'*22,vapid_key_hash='0'*64))
        db.add(RealtimeTicket(user_id=user_id,session_id=session.id,token_hash=uuid4().hex*2,expires_at=utcnow()+timedelta(seconds=30)))
    sys.path.insert(0,str(Path(__file__).parents[2]/'scripts'))
    tool=importlib.import_module('site_backup')
    with engine.begin() as connection:
        tool._normalize(connection,profile=SimpleNamespace(tables={table.name for table in Base.metadata.sorted_tables}))
    with SessionLocal() as db:
        state=db.get(NotificationCounter,user_id)
        assert (state.head_seq,state.read_revision)==(1,1)
        assert db.get(AuthSession,session_id).revoked_at is not None
        assert db.scalar(select(func.count()).select_from(PushSubscription))==0
        assert db.scalar(select(func.count()).select_from(RealtimeTicket))==0
