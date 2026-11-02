import base64
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4
import httpx
import socket
import threading
import time
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from app.main import app
from app.shared.config import settings
from app.shared.db import SessionLocal, utcnow
from app.shared.models import AuthSession, Job, Notification, Outbox, User
from app.shared.security import digest
from app.modules.jobs import service as jobs
from app.modules.notifications import service as push
from app.modules.notifications.models import PushSubscription
from app.shared.outbound import post as real_outbound_post, resolve_public as real_resolve_public
from conftest import invite, login, mutate, register


def b64(raw): return base64.urlsafe_b64encode(raw).decode().rstrip('=')


@pytest.fixture(autouse=True)
def offline_push(monkeypatch):
    # No test can accidentally use a live push provider or developer VAPID key.
    cfg=settings()
    monkeypatch.setattr(cfg,'push_enabled',False)
    monkeypatch.setattr(push.outbound,'resolve_public',lambda host, **kwargs: ('8.8.8.8',))
    monkeypatch.setattr(push.outbound,'post',lambda *a, **kw: (_ for _ in ()).throw(AssertionError('test_must_stub_network')))


@pytest.fixture
def configured(monkeypatch):
    from pydantic import SecretStr
    cfg=settings();key=ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setattr(cfg,'push_enabled',True)
    monkeypatch.setattr(cfg,'push_vapid_public_key',b64(key.public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint)))
    monkeypatch.setattr(cfg,'push_vapid_private_key',SecretStr(b64(key.private_numbers().private_value.to_bytes(32,'big'))))
    monkeypatch.setattr(cfg,'push_vapid_subject','mailto:admin@example.invalid')
    return cfg


def payload(endpoint='https://fcm.googleapis.com/send/capability-secret'):
    key=ec.generate_private_key(ec.SECP256R1())
    return {'endpoint':endpoint,'keys':{'p256dh':b64(key.public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint)), 'auth':b64(b'0123456789abcdef')},'expirationTime':None}


def subscribe(client, data=None):
    response=mutate(client,'PUT','/push/subscriptions',data or payload())
    assert response.status_code==200,response.text
    return response.json()


def notification_job(client):
    response=mutate(client,'POST','/jobs/diagnostic',{'request_key':str(uuid4()),'duration_seconds':0})
    assert response.status_code==202
    task=jobs.claim();assert jobs.finish(task['id'],task['token'],{'ok':True})
    assert jobs.deliver_outbox()==1
    task=jobs.claim();assert task['kind']=='notification.push'
    return task


def test_default_disabled_and_missing_or_mismatched_keys_fail_closed(client,configured,monkeypatch):
    login(client)
    assert client.get('/api/v1/push/config').json()=={'enabled':True,'publicVapid':configured.push_vapid_public_key}
    monkeypatch.setattr(configured,'push_vapid_public_key','')
    assert client.get('/api/v1/push/config').json()=={'enabled':False,'publicVapid':None}
    assert mutate(client,'PUT','/push/subscriptions',payload()).status_code==503
    assert '**********' in repr(configured.push_vapid_private_key)


def test_disabled_still_delivers_inapp_without_push_jobs(client):
    login(client)
    assert client.get('/api/v1/push/config').json()=={'enabled':False,'publicVapid':None}
    task=mutate(client,'POST','/jobs/diagnostic',{'request_key':'push-disabled','duration_seconds':0}).json()
    claimed=jobs.claim();assert jobs.finish(claimed['id'],claimed['token'],{'ok':True})
    assert jobs.deliver_outbox()==1 and jobs.claim() is None
    assert len(client.get('/api/v1/notifications').json())==1


def test_api_is_session_scoped_and_endpoint_never_returned(client,configured,caplog):
    invitation=invite(client,2)
    register(client,invitation['code'])
    data=payload();row=subscribe(client,data)
    assert set(row)=={'id','enabled'}
    assert subscribe(client,data)==row
    assert client.get('/api/v1/push/subscriptions').json()==[row]
    other=TestClient(app);register(other,invitation['code'],'bob')
    assert other.get('/api/v1/push/subscriptions').json()==[]
    assert mutate(other,'PUT','/push/subscriptions',data).status_code==409
    assert mutate(other,'DELETE','/push/subscriptions/'+row['id'],{}).status_code==404
    admin=TestClient(app);login(admin)
    assert mutate(admin,'DELETE','/push/subscriptions/'+row['id'],{}).status_code==404
    same=TestClient(app);login(same,'alice','member-password')
    assert same.get('/api/v1/push/subscriptions').json()==[]
    rebound=subscribe(same,data)
    assert rebound['id']!=row['id']
    assert client.get('/api/v1/push/subscriptions').json()==[]
    assert mutate(client,'DELETE','/push/subscriptions/'+row['id'],{}).status_code==404
    assert mutate(client,'DELETE','/push/subscriptions/'+rebound['id'],{}).status_code==404
    assert 'capability-secret' not in caplog.text
    assert data['keys']['auth'] not in caplog.text
    assert mutate(same,'DELETE','/push/subscriptions/'+rebound['id'],{}).status_code==204
    assert mutate(other,'PUT','/push/subscriptions',data).status_code==200


@pytest.mark.parametrize('change',[lambda d:d.update(extra='x'),lambda d:d.update(endpoint='https://evil.test/x'),
    lambda d:d.update(endpoint='https://fcm.googleapis.com.evil.test/x'),lambda d:d['keys'].update(auth='not-a-key'),
    lambda d:d['keys'].update(p256dh=b64(b'\x04'+b'\0'*64)),lambda d:d['keys'].update(p256dh='x'*10000),
    lambda d:d.update(endpoint='https://fcm.googleapis.com/send/secret?cap=token'),lambda d:d['keys'].update(extra='x')])
def test_strict_input_rejects_keys_hosts_and_unknown_fields(client,configured,change):
    login(client);data=payload();change(data)
    response=mutate(client,'PUT','/push/subscriptions',data)
    assert response.status_code==422 and 'capability-secret' not in response.text
    assert client.get('/api/v1/push/subscriptions').json()==[]


def test_registration_checks_all_dns_and_session_bounds(client,configured,monkeypatch):
    login(client)
    for number in range(3):subscribe(client,payload(f'https://fcm.googleapis.com/send/{number}'))
    assert mutate(client,'PUT','/push/subscriptions',payload('https://fcm.googleapis.com/send/fourth')).status_code==429
    monkeypatch.setattr(push.outbound,'resolve_public',lambda host: (_ for _ in ()).throw(push.outbound.UnsafeDestination()))
    assert mutate(client,'PUT','/push/subscriptions',payload()).status_code==422


def test_transactional_enqueue_is_idempotent_and_payload_has_only_ids(client,configured,monkeypatch):
    login(client);subscribe(client)
    task=notification_job(client)
    assert set(task['payload'])=={'subscription_id','notification_id'}
    with SessionLocal.begin() as db:
        notice=db.get(Notification,UUID(task['payload']['notification_id']))
        push.enqueue_notification(db,notice);push.enqueue_notification(db,notice)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind=='notification.push'))==1
        assert len(db.get(Job,task['id']).dedupe_key)==64
    assert jobs.deliver_outbox()==0


def test_failed_enqueue_rolls_back_notice_and_delivered_marker(client,configured,monkeypatch):
    login(client);subscribe(client)
    mutate(client,'POST','/jobs/diagnostic',{'request_key':'rollback-push','duration_seconds':0})
    task=jobs.claim();jobs.finish(task['id'],task['token'],{'ok':True})
    monkeypatch.setattr(push,'enqueue_notification',lambda *a: (_ for _ in ()).throw(RuntimeError('rollback')))
    with pytest.raises(RuntimeError):jobs.deliver_outbox()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Notification))==0
        assert db.scalar(select(Outbox)).delivered_at is None


def test_actual_encryption_signing_and_generic_content(client,configured,monkeypatch):
    login(client);data=payload();subscribe(client,data);task=notification_job(client);calls=[]
    def post(url,**kwargs):
        calls.append((url,kwargs));return httpx.Response(201)
    monkeypatch.setattr(push.outbound,'post',post)
    assert push.process_job(task)
    assert len(calls)==1
    url,kwargs=calls[0]
    assert url==data['endpoint'] and kwargs['headers']['authorization'].startswith('vapid ')
    assert kwargs['headers']['content-encoding']=='aes128gcm'
    assert push.MESSAGE.encode() not in kwargs['data']
    assert len(kwargs['data'])>len(push.MESSAGE.encode())
    with SessionLocal() as db:
        row=db.get(Job,task['id']);assert row.status=='succeeded' and row.result=={'outcome':'accepted'}
        assert db.scalar(select(func.count()).select_from(Outbox))==1 # push never recursively creates notices


@pytest.mark.parametrize('reason',['logout','expired','disabled','read','deleted','rotated','wrong_owner','wrong_notice'])
def test_dispatch_rechecks_auth_ownership_unread_and_configuration(client,configured,monkeypatch,reason):
    login(client);subscribe(client);task=notification_job(client);calls=[]
    monkeypatch.setattr(push.outbound,'post',lambda *a, **kw:calls.append(a))
    with SessionLocal.begin() as db:
        sub=db.scalar(select(PushSubscription));notice=db.scalar(select(Notification));user=db.get(User,sub.owner_id);session=db.get(AuthSession,sub.session_id)
        if reason=='logout':session.revoked_at=utcnow()
        elif reason=='expired':session.expires_at=utcnow()-timedelta(seconds=1)
        elif reason=='disabled':user.status='disabled'
        elif reason=='read':notice.read_at=utcnow()
        elif reason=='deleted':db.delete(sub)
        elif reason=='rotated':sub.vapid_key_hash='x'*64
        elif reason in {'wrong_owner','wrong_notice'}:
            other=User(username='other',display_name='other',password_hash='unused');db.add(other);db.flush()
            if reason=='wrong_owner':db.get(Job,task['id']).owner_id=other.id
            else:notice.user_id=other.id
    assert push.process_job(task) and calls==[]
    with SessionLocal() as db:assert db.get(Job,task['id']).result=={'outcome':'skipped'}


@pytest.mark.parametrize('status,expected,retry',[(410,'expired',False),(404,'expired',False),(429,'deferred',True),(503,'deferred',True),(307,'rejected',False),(403,'rejected',False),(None,'unknown',True)])
def test_failure_policy_bounded_retry_and_permanent_disable(client,configured,monkeypatch,status,expected,retry):
    login(client);subscribe(client);task=notification_job(client)
    def post(*a,**kw):
        if status is None:raise RuntimeError('secret endpoint response lost after remote acceptance')
        return httpx.Response(status)
    monkeypatch.setattr(push.outbound,'post',post)
    assert push.process_job(task)
    with SessionLocal() as db:
        row=db.get(Job,task['id']);assert row.result=={'outcome':expected}
        assert row.status==('queued' if retry else 'succeeded')
        assert db.scalar(select(PushSubscription)).enabled==(status not in {404,410})
    assert not push.process_job(task) # token consumed, no replay by old worker
    if retry:
        for attempt in range(2):
            with SessionLocal.begin() as db:db.get(Job,task['id']).run_after=utcnow()-timedelta(seconds=1)
            current=jobs.claim();assert push.process_job(current)
        with SessionLocal() as db:
            row=db.get(Job,task['id']);assert row.status=='failed' and row.attempts==3


def test_lease_loss_during_io_cannot_complete_or_replay_old_worker(client,configured,monkeypatch):
    login(client);subscribe(client);task=notification_job(client)
    def post(*a,**kw):
        with SessionLocal.begin() as db:db.get(Job,task['id']).lease_until=utcnow()-timedelta(seconds=1)
        return httpx.Response(201)
    monkeypatch.setattr(push.outbound,'post',post)
    assert not push.process_job(task)
    replacement=jobs.claim();assert replacement['token']!=task['token']
    assert not push.process_job(task)
    with SessionLocal() as db:assert db.get(Job,task['id']).status=='running'


def test_real_logout_and_session_cleanup_block_pending_delivery(client,configured,monkeypatch):
    login(client);subscribe(client);task=notification_job(client)
    assert mutate(client,'POST','/auth/logout',{}).status_code==200
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(PushSubscription))==0
    assert client.get('/api/v1/push/subscriptions').status_code==401
    assert push.process_job(task)
    with SessionLocal() as db:assert db.get(Job,task['id']).result=={'outcome':'skipped'}


def test_push_capabilities_are_explicitly_outside_account_archive():
    from app.modules.exports.service import FIELDS
    assert PushSubscription not in FIELDS


@pytest.mark.parametrize('day',['today','stale','missing','invalid_timezone'])
def test_daily_reminder_rechecks_local_day_before_push(client,configured,monkeypatch,day):
    from zoneinfo import ZoneInfo
    from app.modules.learning.reminder_models import DailyDigest
    login(client);subscribe(client);task=notification_job(client);calls=[]
    monkeypatch.setattr(push.outbound,'post',lambda *a,**kw:(calls.append(a),httpx.Response(201))[1])
    with SessionLocal.begin() as db:
        notice=db.scalar(select(Notification));event=db.get(Outbox,notice.event_id);event.kind='review.daily'
        if day!='missing':
            db.add(DailyDigest(owner_id=notice.user_id,local_date=utcnow().astimezone(ZoneInfo('Asia/Shanghai')).date()-timedelta(days=day=='stale'),
                timezone='invalid/zone' if day=='invalid_timezone' else 'Asia/Shanghai',counts={},job_id=event.job_id))
    assert push.process_job(task)
    assert bool(calls)==(day=='today')


def test_stale_worker_cannot_disable_subscription_after_410(client,configured,monkeypatch):
    login(client);subscribe(client);task=notification_job(client)
    def post(*a,**kw):
        with SessionLocal.begin() as db:db.get(Job,task['id']).worker_token='replacement-worker'
        return httpx.Response(410)
    monkeypatch.setattr(push.outbound,'post',post)
    assert not push.process_job(task)
    with SessionLocal() as db:
        assert db.scalar(select(PushSubscription)).enabled is True
        assert db.get(Job,task['id']).worker_token=='replacement-worker'


def test_cancelled_job_cannot_dispatch(client,configured,monkeypatch):
    login(client);subscribe(client);task=notification_job(client)
    with SessionLocal.begin() as db:db.get(Job,task['id']).status='failed'
    assert not push.process_job(task)


def test_account_archive_excludes_actual_registered_capabilities(client,configured):
    import zipfile
    from app.modules.exports.service import create_archive
    login(client);subscribe(client)
    with SessionLocal() as db:
        sub=db.scalar(select(PushSubscription));owner,session=sub.owner_id,sub.session_id
    with create_archive(owner,session) as archive:
        with zipfile.ZipFile(archive) as data:
            encoded=b''.join(data.read(name) for name in data.namelist())
            for secret in (b'capability-secret',b'p256dh',b'push_subscriptions',b'0123456789abcdef'):
                assert secret not in encoded


def test_retention_understands_push_ids_without_reading_capabilities(client,configured):
    from app.shared.db import engine
    from app.modules.files.retention import collect_references
    login(client);subscribe(client);notification_job(client)
    with engine.connect() as connection:
        result=collect_references(connection)
    assert result['keys']==set() and result['active_writers']==1
    assert 'capability-secret' not in repr(result)


def test_explicit_same_owner_rebind_recovers_closed_tab_and_fences_old_queued_job(client,configured):
    login(client);data=payload();old=subscribe(client,data);queued=notification_job(client)
    fresh=TestClient(app);login(fresh)
    rebound=subscribe(fresh,data)
    assert rebound['id']!=old['id']
    assert fresh.get('/api/v1/push/subscriptions').json()==[rebound]
    assert client.get('/api/v1/push/subscriptions').json()==[]
    assert mutate(client,'DELETE','/push/subscriptions/'+rebound['id'],{}).status_code==404
    assert push.process_job(queued)
    with SessionLocal() as db:
        assert db.get(Job,queued['id']).result=={'outcome':'skipped'}
        assert db.scalar(select(func.count()).select_from(PushSubscription))==1
    assert mutate(client,'POST','/auth/logout',{}).status_code==200
    assert fresh.get('/api/v1/push/subscriptions').json()==[rebound]


def queue_test(client, subscription, key=None):
    return mutate(client,'POST','/push/test',{'request_key':key or str(uuid4()),'subscription_id':subscription['id']})


def test_test_entry_strict_session_scope_and_response_never_returns_capabilities(client,configured):
    login(client);subscription=subscribe(client)
    other=TestClient(app);login(other)
    assert queue_test(other,subscription).status_code==404 # same owner, different session
    invitation=invite(other);member=TestClient(app);register(member,invitation['code'])
    assert queue_test(member,subscription).status_code==404
    assert mutate(client,'POST','/push/test',{'request_key':'invalid','subscription_id':subscription['id']}).status_code==422
    assert mutate(client,'POST','/push/test',{'request_key':str(uuid4()),'subscription_id':subscription['id'],'broadcast':True}).status_code==422
    assert client.post('/api/v1/push/test',json={'request_key':str(uuid4()),'subscription_id':subscription['id']}).status_code==403
    response=queue_test(client,subscription)
    assert response.status_code==202,response.text
    assert response.json()['kind']=='notification.test' and response.json()['status']=='queued'
    assert not {'payload','endpoint','keys','owner_id'} & response.json().keys()
    assert 'capability-secret' not in response.text


def test_test_entry_idempotency_collision_and_queue_bound(client,configured):
    login(client);first=subscribe(client);second=subscribe(client,payload('https://fcm.googleapis.com/send/second'))
    key=str(uuid4());created=queue_test(client,first,key)
    assert queue_test(client,first,key).json()==created.json()
    conflict=queue_test(client,second,key)
    assert conflict.status_code==409 and conflict.json()['code']=='request_key_conflict'
    diagnostic=str(uuid4())
    assert mutate(client,'POST','/jobs/diagnostic',{'request_key':diagnostic,'duration_seconds':0}).status_code==202
    assert queue_test(client,first,diagnostic).status_code==409
    for _ in range(8):assert queue_test(client,first).status_code==202
    assert queue_test(client,first).status_code==429
    assert queue_test(client,first,key).json()==created.json() # retry works even at quota
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Outbox))==0
        assert db.scalar(select(func.count()).select_from(Notification))==0


@pytest.mark.parametrize('reason',['disabled','rotated','config'])
def test_test_entry_rejects_unusable_push(client,configured,monkeypatch,reason):
    login(client);subscription=subscribe(client)
    with SessionLocal.begin() as db:
        row=db.get(PushSubscription,UUID(subscription['id']))
        if reason=='disabled':row.enabled=False
        elif reason=='rotated':row.vapid_key_hash='x'*64
    if reason=='config':monkeypatch.setattr(configured,'push_enabled',False)
    assert queue_test(client,subscription).status_code==(503 if reason=='config' else 409)
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(Job))==0


def test_test_worker_creates_real_notice_and_only_current_device_queue_idempotently(client,configured,monkeypatch):
    from app import worker
    login(client);target=subscribe(client)
    subscribe(client,payload('https://fcm.googleapis.com/send/same-session-second'))
    other=TestClient(app);login(other);subscribe(other,payload('https://fcm.googleapis.com/send/other-session'))
    key=str(uuid4());response=queue_test(client,target,key);assert response.status_code==202
    claimed=jobs.claim();assert claimed['kind']=='notification.test'
    worker.execute(claimed)
    assert not push.process_test_job(claimed)
    with SessionLocal() as db:
        event=db.scalar(select(Outbox));assert event.kind=='notification.test'
        assert event.payload['subscription_id']==target['id']
        assert '自检' not in event.payload['message']
        assert db.scalar(select(func.count()).select_from(Notification))==0
    assert jobs.deliver_outbox()==1 and jobs.deliver_outbox()==0
    assert len(client.get('/api/v1/notifications').json())==1
    with SessionLocal.begin() as db:
        notice=db.scalar(select(Notification));assert notice.message=='知页浏览器提醒测试：请以站内提醒记录为准'
        push.enqueue_notification(db,notice);push.enqueue_notification(db,notice)
        tasks=db.scalars(select(Job).where(Job.kind=='notification.push')).all()
        assert len(tasks)==1 and tasks[0].payload['subscription_id']==target['id']
        # Simulate an admin retry of the producer after a terminal failure.
        db.get(Job,claimed['id']).status='failed'
    assert mutate(client,'POST','/admin/jobs/'+str(claimed['id'])+'/retry',{}).status_code==200
    with SessionLocal.begin() as db:db.get(Job,claimed['id']).run_after=utcnow()-timedelta(seconds=1)
    worker.execute(jobs.claim())
    assert jobs.deliver_outbox()==0
    assert queue_test(client,target,key).json()['id']==response.json()['id']
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Notification))==1
        assert db.scalar(select(func.count()).select_from(Outbox))==1
    calls=[]
    monkeypatch.setattr(push.outbound,'post',lambda url,**kw:(calls.append((url,kw)),httpx.Response(201))[1])
    task=jobs.claim()
    assert push.process_job(task)
    assert len(calls)==1 and calls[0][0]=='https://fcm.googleapis.com/send/capability-secret'
    assert calls[0][1]['headers']['content-encoding']=='aes128gcm'


@pytest.mark.parametrize('reason',['deleted','rebound','logout','expired','disabled_user','disabled_sub','rotated','config','cancelled','lease'])
def test_test_producer_rechecks_binding_and_lease_before_notice(client,configured,monkeypatch,reason):
    login(client);data=payload();subscription=subscribe(client,data)
    assert queue_test(client,subscription).status_code==202
    task=jobs.claim()
    if reason=='rebound':
        fresh=TestClient(app);login(fresh);subscribe(fresh,data)
    elif reason=='logout':assert mutate(client,'POST','/auth/logout',{}).status_code==200
    else:
        with SessionLocal.begin() as db:
            sub=db.get(PushSubscription,UUID(subscription['id']))
            if reason=='deleted':db.delete(sub)
            elif reason=='expired':db.get(AuthSession,sub.session_id).expires_at=utcnow()-timedelta(seconds=1)
            elif reason=='disabled_user':db.get(User,sub.owner_id).status='disabled'
            elif reason=='disabled_sub':sub.enabled=False
            elif reason=='rotated':sub.vapid_key_hash='x'*64
            elif reason=='cancelled':db.get(Job,task['id']).status='failed'
            elif reason=='lease':db.get(Job,task['id']).lease_until=utcnow()-timedelta(seconds=1)
    if reason=='config':monkeypatch.setattr(configured,'push_enabled',False)
    assert push.process_test_job(task)==(reason not in {'cancelled','lease'})
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Outbox))==0
        assert db.scalar(select(func.count()).select_from(Notification))==0


def test_test_outbox_does_not_broadcast_after_target_removed(client,configured):
    login(client);target=subscribe(client);subscribe(client,payload('https://fcm.googleapis.com/send/second'))
    queue_test(client,target);assert push.process_test_job(jobs.claim())
    assert mutate(client,'DELETE','/push/subscriptions/'+target['id'],{}).status_code==204
    assert jobs.deliver_outbox()==1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Notification))==1
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind=='notification.push'))==0


def test_test_producer_rollback_and_fencing_cannot_publish_an_uncommitted_or_stale_notice(client,configured,monkeypatch):
    login(client);target=subscribe(client);queue_test(client,target);task=jobs.claim()
    finish=push.finish_row
    monkeypatch.setattr(push,'finish_row',lambda *a:(_ for _ in ()).throw(RuntimeError('transaction interrupted')))
    with pytest.raises(RuntimeError,match='transaction interrupted'):push.process_test_job(task)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Outbox))==0
        assert db.get(Job,task['id']).status=='running'
    monkeypatch.setattr(push,'finish_row',finish)
    key=push.vapid()
    def lost_lease():
        with SessionLocal.begin() as db:db.get(Job,task['id']).worker_token='replacement'
        return key
    monkeypatch.setattr(push,'vapid',lost_lease)
    assert not push.process_test_job(task)
    with SessionLocal() as db:assert db.scalar(select(func.count()).select_from(Outbox))==0


def test_test_dispatch_rejects_modified_target_and_retention_handles_test_kind(client,configured,monkeypatch):
    from app.shared.db import engine
    from app.modules.files.retention import collect_references
    login(client);first=subscribe(client);second=subscribe(client,payload('https://fcm.googleapis.com/send/second'))
    queue_test(client,first)
    with engine.connect() as connection:assert collect_references(connection)['active_writers']==1
    assert push.process_test_job(jobs.claim()) and jobs.deliver_outbox()==1
    task=jobs.claim()
    with SessionLocal.begin() as db:
        job=db.get(Job,task['id']);job.payload={**job.payload,'subscription_id':second['id']}
    calls=[];monkeypatch.setattr(push.outbound,'post',lambda *a,**kw:calls.append(a))
    assert push.process_job(task) and calls==[]
    with SessionLocal() as db:assert db.get(Job,task['id']).result=={'outcome':'skipped'}


@pytest.mark.parametrize('reason',['cancelled','token','lease','expired_session','config','valid'])
def test_final_transport_gate_rechecks_after_dns_before_any_post(client,configured,monkeypatch,reason):
    login(client);subscribe(client);task=notification_job(client);sent=[]
    clock=[utcnow()]
    if reason=='expired_session':
        with SessionLocal.begin() as db:
            sub=db.scalar(select(PushSubscription));db.get(AuthSession,sub.session_id).expires_at=clock[0]+timedelta(seconds=1)
        monkeypatch.setattr(push,'utcnow',lambda:clock[0])
    def resolve(*args,**kwargs):
        if reason in {'cancelled','token','lease'}:
            with SessionLocal.begin() as db:
                job=db.get(Job,task['id'])
                if reason=='cancelled':job.status='failed'
                elif reason=='token':job.worker_token='replacement-worker'
                else:job.lease_until=utcnow()-timedelta(seconds=1)
        elif reason=='expired_session':clock[0]+=timedelta(seconds=2)
        elif reason=='config':configured.push_enabled=False
        return [(socket.AF_INET,1,6,'',('8.8.8.8',443))]
    class Transport:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def handle_request(self,request):sent.append(request);return httpx.Response(201)
    monkeypatch.setattr(socket,'getaddrinfo',resolve)
    monkeypatch.setattr(httpx,'HTTPTransport',Transport)
    monkeypatch.setattr(push.outbound,'post',real_outbound_post)
    monkeypatch.setattr(push.outbound,'resolve_public',real_resolve_public)
    result=push.process_job(task)
    if reason=='valid':
        assert result and len(sent)==1
        assert sent[0].url.host=='8.8.8.8' and sent[0].headers['host']=='fcm.googleapis.com'
        assert sent[0].extensions['sni_hostname']=='fcm.googleapis.com'
        assert sent[0].headers['content-encoding']=='aes128gcm'
        with SessionLocal() as db:assert db.get(Job,task['id']).result=={'outcome':'accepted'}
        return
    assert sent==[], 'DNS期间失效后不能开始任何实际POST'
    if reason in {'cancelled','token','lease'}:assert not result
    else:
        assert result
        with SessionLocal() as db:assert db.get(Job,task['id']).result=={'outcome':'skipped'}


def test_dns_timeout_defers_boundedly_without_send_and_releases_subscription_locks(client,configured,monkeypatch):
    login(client);subscription=subscribe(client);task=notification_job(client)
    release,finished=threading.Event(),threading.Event();calls=[]
    def resolve(*args,**kwargs):
        try:
            release.wait()
            return [(socket.AF_INET,1,6,'',('8.8.8.8',443))]
        finally:finished.set()
    monkeypatch.setattr(push.outbound,'_resolver_pool',push.outbound._ResolverPool(workers=1))
    monkeypatch.setattr(push.outbound,'DNS_TIMEOUT_SECONDS',.03)
    monkeypatch.setattr(socket,'getaddrinfo',resolve)
    monkeypatch.setattr(push.outbound,'post',real_outbound_post)
    monkeypatch.setattr(push.outbound,'resolve_public',real_resolve_public)
    monkeypatch.setattr(httpx,'HTTPTransport',lambda **kw:calls.append(kw))
    try:
        started=time.monotonic();assert push.process_job(task)
        assert time.monotonic()-started<2 and calls==[]
        with SessionLocal() as db:
            row=db.get(Job,task['id'])
            assert row.status=='queued' and row.result=={'outcome':'deferred'} and row.error_code=='push_endpoint_unavailable'
            assert row.worker_token is None and row.lease_until is None
        # The OS lookup is still blocked, but neither DB locks nor a send are.
        assert not release.is_set()
        assert mutate(client,'DELETE','/push/subscriptions/'+subscription['id'],{}).status_code==204
    finally:
        release.set();assert finished.wait(1)
    assert calls==[] # late DNS completion cannot replay the cancelled call


@pytest.mark.parametrize('change',['midnight','session','lease','config','rotated_key','valid'])
def test_daily_final_gate_rechecks_pure_deadlines_and_config_after_last_lease_sql(client,configured,monkeypatch,change):
    from zoneinfo import ZoneInfo
    from sqlalchemy import event
    from app import worker
    from app.shared.db import engine
    from app.modules.learning import reminder_service
    from app.modules.learning.reminder_models import DailyDigest
    from test_reminders import prepare

    zone=ZoneInfo('Asia/Shanghai')
    base=utcnow().astimezone(zone).replace(hour=23,minute=59,second=59,microsecond=0)
    clock=[base]
    prepare(client,now=base)
    subscribe(client)
    monkeypatch.setattr(push,'utcnow',lambda:clock[0])
    monkeypatch.setattr(jobs,'utcnow',lambda:clock[0])
    assert reminder_service.dispatch(base)==1
    assert jobs.deliver_outbox()==1
    task=jobs.claim();assert task['kind']=='notification.push'
    session_expires=base+timedelta(seconds=1 if change=='session' else 60)
    lease_expires=base+timedelta(seconds=1 if change=='lease' else 60)
    with SessionLocal.begin() as db:
        db.get(Job,task['id']).lease_until=lease_expires
        sub=db.scalar(select(PushSubscription));db.get(AuthSession,sub.session_id).expires_at=session_expires
        daily=db.scalar(select(DailyDigest));assert daily.timezone=='Asia/Shanghai' and daily.local_date==base.date()
    new_key=ec.generate_private_key(ec.SECP256R1())
    lease_reads=[];queries=[];sent=[]
    def after_sql(connection,cursor,statement,parameters,context,executemany):
        queries.append(statement)
        if statement.lstrip().startswith('SELECT jobs.lease_until'):
            lease_reads.append(len(queries))
            if len(lease_reads)==2:
                # The DB returned a lease valid at query start. All final
                # predicates must use time/config AFTER this possible wait.
                if change in {'midnight','session','lease'}:clock[0]+=timedelta(seconds=2)
                elif change=='config':configured.push_enabled=False
                elif change=='rotated_key':
                    from pydantic import SecretStr
                    configured.push_vapid_public_key=b64(new_key.public_key().public_bytes(Encoding.X962,PublicFormat.UncompressedPoint))
                    configured.push_vapid_private_key=SecretStr(b64(new_key.private_numbers().private_value.to_bytes(32,'big')))
    class Transport:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def handle_request(self,request):
            sent.append(request)
            # No hidden DailyDigest SELECT may appear after the final lease
            # read; these independent deadline checks describe send eligibility.
            assert len(lease_reads)==2 and len(queries)==lease_reads[-1]
            assert clock[0].astimezone(zone).date()==base.date()
            assert clock[0]<session_expires and clock[0]<lease_expires
            assert push.vapid() is not None
            return httpx.Response(201)
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,1,6,'',('8.8.8.8',443))])
    monkeypatch.setattr(httpx,'HTTPTransport',Transport)
    monkeypatch.setattr(push.outbound,'post',real_outbound_post)
    monkeypatch.setattr(push.outbound,'resolve_public',real_resolve_public)
    event.listen(engine,'after_cursor_execute',after_sql)
    try:worker.execute(task)
    finally:event.remove(engine,'after_cursor_execute',after_sql)
    assert len(lease_reads)==2
    if change=='valid':
        assert len(sent)==1
        request=sent[0]
        assert request.headers['content-encoding']=='aes128gcm' and request.headers['authorization'].startswith('vapid ')
        assert push.MESSAGE.encode() not in request.content
        with SessionLocal() as db:assert db.get(Job,task['id']).result=={'outcome':'accepted'}
    else:
        assert sent==[], '最后lease SQL期间跨越截止时间或配置失效，不能开始POST'
        with SessionLocal() as db:
            row=db.get(Job,task['id'])
            if change=='lease':assert row.status=='running' and row.result is None
            else:assert row.status=='succeeded' and row.result=={'outcome':'skipped'}
