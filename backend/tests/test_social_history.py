import hashlib
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.shared.db import SessionLocal
from app.shared.models import User
from app.shared.config import settings
from app.shared.security import hasher
from app.modules.exports.social_models import SocialArchiveAsset, SocialArchiveRecord
from app.modules.files.service import storage
from conftest import login


@pytest.fixture
def history(tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), 'storage_root', str(tmp_path))
    with SessionLocal.begin() as db:
        owner = db.scalar(select(User).where(User.username == 'admin'))
        other = User(username='history_peer', display_name='合成成员', password_hash=hasher.hash('member-password'))
        db.add(other)
        db.flush()
        first = SocialArchiveRecord(owner_id=owner.id, kind='chat_message', source_id=uuid4(), payload={'offline_only':True,'body':'恢复的完整消息'})
        foreign = SocialArchiveRecord(owner_id=other.id, kind='chat_message', source_id=uuid4(), payload={'offline_only':True,'body':'其他账号私人历史'})
        db.add_all([first,foreign]);db.flush()
        key=f'restore_fixture/{uuid4()}/original'
        content=b'archived synthetic bytes'
        storage().put(key,content)
        asset=SocialArchiveAsset(owner_id=owner.id,record_id=first.id,source_id=uuid4(),role='attachment',object_key=key,filename='history.txt',mime='text/plain',bytes=len(content),sha256=hashlib.sha256(content).hexdigest())
        db.add(asset);db.flush()
        return {'own':str(first.id),'foreign':str(foreign.id),'asset':str(asset.id),'key':key,'data':content}


def test_recovered_history_owner_only_and_no_storage_keys(client,history):
    login(client)
    rows=client.get('/api/v1/social/archive/records').json()['items']
    assert [row['id'] for row in rows] == [history['own']]
    own=client.get('/api/v1/social/archive/records/'+history['own'])
    assert own.status_code==200 and own.json()['payload']['body']=='恢复的完整消息'
    assert 'object_key' not in own.text and history['key'] not in own.text
    assert client.get('/api/v1/social/archive/records/'+history['foreign']).status_code==404


def test_recovered_assets_require_owner_and_verify_real_bytes(client,history):
    login(client)
    path='/api/v1/social/archive/assets/'+history['asset']+'/content'
    assert client.get(path).content == history['data']
    storage().put(history['key'],b'X'*len(history['data']))
    assert client.get(path).status_code==503
    login(client,'history_peer','member-password')
    assert client.get(path).status_code==404


def test_history_is_readonly_and_anonymous_denied(client,history):
    assert client.get('/api/v1/social/archive/records').status_code==401
    login(client)
    assert client.get('/api/v1/social/archive/records?kind=not-a-kind').status_code==422
    assert client.get('/api/v1/social/archive/records?kind=profile').json()['items']==[]
    assert client.get('/api/v1/social/archive/records?limit=0').status_code==422
    assert client.post('/api/v1/social/archive/records/'+history['own'],headers={'Origin':'http://testserver'},json={}).status_code==405
