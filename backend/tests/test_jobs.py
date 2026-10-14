from datetime import timedelta
from sqlalchemy import select, func
from fastapi.testclient import TestClient
from app.main import app
from app.shared.db import SessionLocal, utcnow
from app.shared.models import Job, Outbox, Notification
from app.modules.jobs import service
from app.worker import tick
from conftest import mutate, invite, register, login


def create_job(client,key='request-001'):
    r=mutate(client,'POST','/jobs/diagnostic',{'request_key':key,'duration_seconds':0})
    assert r.status_code==202,r.text
    return r.json()


def expire(job_id):
    from uuid import UUID
    with SessionLocal.begin() as db:
        db.get(Job,UUID(job_id)).lease_until=utcnow()-timedelta(seconds=1)


def test_idempotent_job_and_outbox(client):
    login(client)
    job=create_job(client)
    assert create_job(client)['id']==job['id']
    assert mutate(client,'POST','/jobs/diagnostic',{'request_key':'request-001','duration_seconds':1}).status_code==409
    assert tick()
    assert client.get('/api/v1/jobs/'+job['id']).json()['status']=='succeeded'
    notices=client.get('/api/v1/notifications').json()
    assert len(notices)==1
    assert mutate(client,'POST','/notifications/'+notices[0]['id']+'/read',{}).json()['read_at']
    assert service.deliver_outbox()==0
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Outbox))==1
        assert db.scalar(select(func.count()).select_from(Notification))==1


def test_private_job_isolation_including_admin(client):
    created=invite(client,2)
    register(client,created['code'])
    job=create_job(client)
    other=TestClient(app)
    register(other,created['code'],'bob')
    assert other.get('/api/v1/jobs/'+job['id']).status_code==404
    assert other.get('/api/v1/jobs').json()==[]
    admin=TestClient(app)
    login(admin)
    assert admin.get('/api/v1/jobs/'+job['id']).status_code==404
    assert 'payload' not in admin.get('/api/v1/admin/jobs').json()[0]


def test_expired_lease_recovery_and_stale_worker_fencing(client):
    login(client)
    job=create_job(client)
    first=service.claim()
    assert first
    expire(job['id'])
    assert not service.heartbeat(first['id'],first['token'])
    second=service.claim()
    assert second and second['token']!=first['token']
    assert not service.finish(first['id'],first['token'],{'ok':False})
    assert service.finish(second['id'],second['token'],{'ok':True})
    assert not service.finish(second['id'],second['token'],{'ok':True})
    service.deliver_outbox()
    assert len(client.get('/api/v1/notifications').json())==1
    assert client.get('/api/v1/jobs/'+job['id']).json()['attempts']==2


def test_retry_backoff_and_max_attempts(client):
    login(client)
    job=create_job(client)
    from uuid import UUID
    for attempt in range(3):
        claimed=service.claim()
        assert claimed
        assert service.fail(claimed['id'],claimed['token'])
        row=client.get('/api/v1/jobs/'+job['id']).json()
        if attempt<2:
            assert row['status']=='queued'
            assert service.claim() is None
            with SessionLocal.begin() as db:db.get(Job,UUID(job['id'])).run_after=utcnow()-timedelta(seconds=1)
    assert row['status']=='failed'
    assert mutate(client,'POST','/admin/jobs/'+job['id']+'/retry',{}).json()['attempts']==0
    assert tick()


def test_exhausted_lease_is_terminal(client):
    login(client)
    job=create_job(client)
    from uuid import UUID
    for _ in range(3):
        assert service.claim()
        expire(job['id'])
    assert service.claim() is None
    assert client.get('/api/v1/jobs/'+job['id']).json()['status']=='failed'


def test_queue_bound(client):
    login(client)
    for i in range(10):create_job(client,f'request-{i:03}')
    assert mutate(client,'POST','/jobs/diagnostic',{'request_key':'request-extra','duration_seconds':0}).status_code==429


def test_failure_rolls_back_outbox(client):
    login(client)
    create_job(client)
    first=service.claim()
    assert service.fail(first['id'],first['token'])
    assert service.deliver_outbox()==0
    assert client.get('/api/v1/notifications').json()==[]
