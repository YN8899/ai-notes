from datetime import timedelta
from sqlalchemy import select, func
from fastapi.testclient import TestClient
from app.main import app
from app.shared.db import SessionLocal, utcnow
from app.shared.models import Invite, AuthSession, Audit, User
from app.shared.security import digest
from app.shared.config import settings
from conftest import mutate, invite, register, login


def test_invite_registration_and_session_revocation(client):
    created=invite(client)
    assert register(client,created['code']).status_code==201
    raw=client.cookies.get('notes_session')
    assert client.get('/api/v1/auth/me').json()['role']=='user'
    with SessionLocal() as db:
        stored=db.scalar(select(AuthSession).where(AuthSession.token_hash==digest(raw)))
        assert stored and stored.token_hash!=raw
        assert db.scalar(select(Invite)).used_count==1
        assert db.scalar(select(Invite)).code_hash!=created['code']
    assert mutate(client,'POST','/auth/logout',{}).status_code==200
    client.cookies.set('notes_session',raw)
    assert client.get('/api/v1/auth/me').status_code==401
    assert register(client,created['code'],'bob').status_code==400


def test_origin_csrf_and_validation_redaction(client):
    assert client.post('/api/v1/auth/login',json={'username':'admin','password':'secret'}).status_code==403
    assert client.post('/api/v1/auth/login',headers={'Origin':'http://testserver'},json={'username':'admin','password':'secret'}).status_code==403
    login(client)
    csrf=client.cookies.get('notes_csrf')
    r=client.post('/api/v1/jobs/diagnostic',headers={'Origin':'http://testserver','X-CSRF-Token':'wrong'},json={'request_key':'abcdefgh','duration_seconds':0})
    assert r.status_code==403
    assert csrf
    r=mutate(client,'POST','/auth/login',{'username':'!','password':'super-sensitive-value'})
    assert r.status_code==422 and 'super-sensitive-value' not in r.text
    assert r.json()['request_id']==r.headers['x-request-id']


def test_registration_does_not_spend_invite_on_duplicate_name(client):
    created=invite(client,2)
    assert register(client,created['code']).status_code==201
    assert register(client,created['code']).status_code==409
    with SessionLocal() as db:
        assert db.scalar(select(Invite)).used_count==1


def test_disabled_expired_invites(client):
    created=invite(client)
    assert mutate(client,'PATCH','/admin/invites/'+created['id'],{'disabled':True}).status_code==200
    assert register(client,created['code']).status_code==400
    mutate(client,'PATCH','/admin/invites/'+created['id'],{'disabled':False})
    with SessionLocal.begin() as db:
        db.scalar(select(Invite)).expires_at=utcnow()-timedelta(seconds=1)
    assert register(client,created['code']).status_code==400


def test_admin_permissions_disable_and_reset(client):
    created=invite(client)
    member=register(client,created['code']).json()
    assert client.get('/api/v1/admin/invites').status_code==403
    assert client.get('/api/v1/admin/audit').status_code==403
    raw=client.cookies.get('notes_session')
    admin=TestClient(app)
    login(admin)
    reset=mutate(admin,'POST','/admin/users/'+member['id']+'/reset-token',{}).json()
    assert mutate(client,'POST','/auth/password/reset',{'token':reset['token'],'password':'replacement-password'}).status_code==200
    assert client.get('/api/v1/auth/me').status_code==401
    assert mutate(client,'POST','/auth/password/reset',{'token':reset['token'],'password':'replacement-password'}).status_code==400
    assert mutate(client,'POST','/auth/login',{'username':'alice','password':'member-password'}).status_code==401
    login(client,'alice','replacement-password')
    assert mutate(admin,'PATCH','/admin/users/'+member['id'],{'status':'disabled'}).status_code==200
    assert client.get('/api/v1/auth/me').status_code==401
    assert mutate(client,'POST','/auth/login',{'username':'alice','password':'replacement-password'}).status_code==401
    assert mutate(admin,'PATCH','/admin/users/'+member['id'],{'status':'active'}).status_code==200
    client.cookies.set('notes_session',raw)
    assert client.get('/api/v1/auth/me').status_code==401
    with SessionLocal() as db:
        for row in db.scalars(select(Audit)):
            assert reset['token'] not in str(row.details)


def test_auth_rate_limit_persists_failures(client):
    cfg=settings()
    old=cfg.rate_limit_requests
    cfg.rate_limit_requests=2
    try:
        for _ in range(2):
            assert mutate(client,'POST','/auth/login',{'username':'bad','password':'bad'}).status_code==401
        assert mutate(client,'POST','/auth/login',{'username':'bad','password':'bad'}).status_code==429
    finally:
        cfg.rate_limit_requests=old


def test_expired_session(client):
    login(client)
    with SessionLocal.begin() as db:
        db.scalar(select(AuthSession)).expires_at=utcnow()-timedelta(seconds=1)
    assert client.get('/api/v1/auth/me').status_code==401


def test_admin_cannot_disable_self(client):
    user=login(client)
    assert mutate(client,'PATCH','/admin/users/'+user['id'],{'status':'disabled'}).status_code==400
