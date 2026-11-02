"""Real PostgreSQL regression: auth cannot wait on the shared HTTP pool."""
import asyncio
from datetime import timedelta
from uuid import uuid4
import psycopg
import pytest
from app.shared.db import SessionLocal, engine, utcnow
from app.shared.models import AuthSession, User
from app.modules.social.realtime_hub import Hub, validate_identities


@pytest.fixture
def auth_identity():
    with SessionLocal.begin() as db:
        user=User(username='authpool_'+uuid4().hex[:12],display_name='synthetic',password_hash='unusable')
        db.add(user);db.flush()
        session=AuthSession(user_id=user.id,token_hash=uuid4().hex*2,csrf_hash=uuid4().hex*2,expires_at=utcnow()+timedelta(hours=1))
        db.add(session);db.flush()
        return user.id,session.id


def auth_connection_count():
    url=engine.url.set(drivername='postgresql').render_as_string(hide_password=False)
    with psycopg.connect(url,autocommit=True) as connection:
        return connection.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND application_name='notes-realtime-auth'").fetchone()[0]


def test_shared_pool_exhaustion_does_not_keep_revoked_socket_open(auth_identity):
    assert validate_identities({auth_identity})=={auth_identity}
    url=engine.url.set(drivername='postgresql').render_as_string(hide_password=False)
    with psycopg.connect(url,autocommit=True) as connection:
        connection.execute('UPDATE sessions SET revoked_at=now() WHERE id=%s',(auth_identity[1],))
    held=[engine.connect() for _ in range(engine.pool.size()+engine.pool._max_overflow)]
    async def scenario():
        hub=Hub();await hub.start();sub=hub.subscribe(auth_identity);sub.frames.get_nowait()
        hub.task=asyncio.create_task(hub.run())
        try:
            frame=await asyncio.wait_for(sub.frames.get(),1.6)
            assert frame=={'type':'close','code':1008} and sub not in hub.subscriptions
        finally:
            await hub.stop()
        assert hub.owned_validator.pool.checkedout()==0 and auth_connection_count()==0
    try:asyncio.run(scenario())
    finally:
        for connection in held:connection.close()
    assert engine.pool.checkedout()==0


def test_private_auth_pool_checkout_is_finite_and_fails_closed(auth_identity):
    async def scenario():
        hub=Hub();await hub.start();assert hub.validator({auth_identity})=={auth_identity}
        held=hub.owned_validator.pool.connect()
        sub=hub.subscribe(auth_identity);sub.frames.get_nowait();hub.task=asyncio.create_task(hub.run())
        try:
            assert (await asyncio.wait_for(sub.frames.get(),.6))['code']==1008
        finally:
            held.close();await hub.stop()
        assert hub.owned_validator.pool.checkedout()==0 and auth_connection_count()==0
    asyncio.run(scenario())


def test_shutdown_drains_real_statement_timeout_before_disposing(auth_identity):
    async def scenario():
        hub=Hub();await hub.start();assert hub.validator({auth_identity})=={auth_identity}
        url=engine.url.set(drivername='postgresql').render_as_string(hide_password=False)
        with psycopg.connect(url) as lock:
            lock.execute('LOCK TABLE sessions IN ACCESS EXCLUSIVE MODE')
            sub=hub.subscribe(auth_identity);sub.frames.get_nowait();hub.task=asyncio.create_task(hub.run())
            await asyncio.sleep(.1)
            await asyncio.wait_for(hub.stop(),1.6)
            assert sub.frames.get_nowait()['code']==1012
        assert hub.validation is None or hub.validation.done()
        assert hub.owned_validator.pool.checkedout()==0 and auth_connection_count()==0
    asyncio.run(scenario())


def test_positive_owner_expiry_and_disabled_checks_stay_authoritative(auth_identity):
    assert validate_identities({auth_identity})=={auth_identity}
    assert not validate_identities({(uuid4(),auth_identity[1])})
    with SessionLocal.begin() as db:db.get(AuthSession,auth_identity[1]).expires_at=utcnow()-timedelta(seconds=1)
    assert not validate_identities({auth_identity})
    with SessionLocal.begin() as db:
        db.get(AuthSession,auth_identity[1]).expires_at=utcnow()+timedelta(hours=1)
        db.get(User,auth_identity[0]).status='disabled'
    assert not validate_identities({auth_identity})
    assert auth_connection_count()==0
