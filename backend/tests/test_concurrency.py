import os
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from app.main import app
from app.shared.db import SessionLocal
from app.shared.models import User, Invite
from app.modules.jobs import service
from conftest import invite, register, login, mutate

pytestmark=pytest.mark.skipif(os.getenv('PGLITE_TEST')=='1',reason='PGlite has one backend; native PostgreSQL concurrency is checked by CI')


def test_last_invite_slot_concurrently(client):
    created=invite(client)
    barrier=threading.Barrier(2)
    def attempt(name):
        with TestClient(app) as c:
            barrier.wait()
            return register(c,created['code'],name).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(attempt,['alice','bob']))
    assert sorted(results)==[201,400]
    with SessionLocal() as db:
        assert db.scalar(select(Invite)).used_count==1
        assert db.scalar(select(func.count()).select_from(User))==2


def test_workers_only_claim_each_job_once(client):
    login(client)
    for i in range(5):
        assert mutate(client,'POST','/jobs/diagnostic',{'request_key':f'request-{i:03}','duration_seconds':0}).status_code==202
    with ThreadPoolExecutor(max_workers=5) as ex:
        claims=list(ex.map(lambda _:service.claim(),range(5)))
    assert len({c['id'] for c in claims if c})==5
