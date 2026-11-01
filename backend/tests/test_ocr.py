"""OCR is an explicit, private, at-most-once page operation, never an upload side effect."""
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.modules.ai import chat_provider, chat_service
from app.modules.ai.models import AiConversation, AiRun
from app.modules.files.models import File, NotePage
from app.modules.notes.models import Note
from app.shared.config import settings
from app.shared.db import SessionLocal, utcnow
from app.shared.models import User
from app.shared.security import hasher
from conftest import login, mutate
from test_image_chat import image_book, image_source, mock
from test_pdf_chat import pdf_book


@pytest.fixture(autouse=True)
def private_config(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ocr-fake-test-key")
    settings.cache_clear()
    monkeypatch.setattr(settings(), "storage_root", str(tmp_path / "private"))
    monkeypatch.setattr(chat_provider, "create_client", lambda: (_ for _ in ()).throw(AssertionError("No live provider")))
    yield
    settings.cache_clear()


def request(client, nid, *, pdf=False, **changes):
    if pdf:
        detail = client.get('/api/v1/ai/pdf-notebooks/' + nid).json()
        source = {"note_id": nid, "revision": detail["revision"], "page_id": detail["pages"][0]["page_id"]}
    else:
        source = image_source(client, nid)
    return {**source, "request_key": str(uuid4()), **changes}


def create(client, body):
    return mutate(client, "POST", "/ocr/documents", body)


def read(client, row):
    response = client.get('/api/v1/ocr/documents/' + row['id'])
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('pdf', [False, True])
@pytest.mark.parametrize('region', [None, {"x": .5, "y": 0, "width": .5, "height": 1}])
def test_one_page_roundtrip_correction_and_hidden_chat(client, monkeypatch, pdf, region):
    login(client)
    nid = pdf_book(client) if pdf else image_book(client)
    body = request(client, nid, pdf=pdf, region=region)
    response = create(client, body)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row['status'] == 'queued' and row['raw_text'] is None
    assert create(client, body).json()['id'] == row['id']
    assert create(client, {**body, 'region': {"x": 0, "y": 0, "width": .5, "height": .5}}).status_code == 409
    calls = mock(monkeypatch, answer='中文手写原文 $x^2$')
    assert chat_service.tick() and not chat_service.tick()
    done = read(client, row)
    assert done['status'] == 'succeeded' and done['raw_text'] == '中文手写原文 $x^2$'
    assert done['source_valid'] and done['region'] == region and len(calls) == 1
    assert '直接依据所选页图回答' not in str(calls[0])
    assert '每页尽量80至120字' not in str(calls[0])
    assert client.get('/api/v1/ai/conversations').json() == []
    with SessionLocal() as db:
        run = db.get(AiRun, UUID(row['run_id']))
        cid = str(run.conversation_id)
    assert mutate(client, 'POST', f'/ai/conversations/{cid}/runs', {'prompt': '不要追加', 'request_key': str(uuid4())}).status_code == 404
    edited = mutate(client, 'PATCH', '/ocr/documents/' + row['id'], {'revision': 1, 'corrected_text': '人工纠正文字', 'reviewed': True})
    assert edited.status_code == 200, edited.text
    assert edited.json()['revision'] == 2 and edited.json()['raw_text'] == done['raw_text']
    assert mutate(client, 'PATCH', '/ocr/documents/' + row['id'], {'revision': 1, 'corrected_text': '旧草稿', 'reviewed': False}).status_code == 409
    assert read(client, row)['corrected_text'] == '人工纠正文字'
    other = create(client, {**body, 'request_key': str(uuid4())})
    assert other.status_code == 201 and other.json()['id'] != row['id']
    assert read(client, row)['corrected_text'] == '人工纠正文字'
    listed = client.get('/api/v1/ocr/documents', params={'note_id': nid, 'page_id': body['page_id'], 'limit': 1}).json()
    assert len(listed) == 1
    assert 'object_key' not in response.text and 'request_hash' not in response.text


@pytest.mark.parametrize('change', ['deleted', 'file', 'sha', 'dimensions'])
def test_changed_source_before_execution_stops_call(client, monkeypatch, change):
    login(client)
    nid = image_book(client)
    body = request(client, nid)
    response = create(client, body)
    assert response.status_code == 201, response.text
    row = response.json()
    with SessionLocal.begin() as db:
        note = db.get(Note, UUID(nid))
        page = db.get(NotePage, UUID(body['page_id']))
        file = db.get(File, page.file_id)
        if change == 'deleted': note.deleted_at = utcnow()
        elif change == 'sha': file.sha256 = 'b' * 64
        elif change == 'dimensions': file.width += 1
        else: db.delete(page)
    calls = mock(monkeypatch)
    assert chat_service.tick()
    assert not calls
    done = read(client, row)
    assert not done['source_valid'] and done['status'] != 'succeeded'
    assert mutate(client, 'PATCH', '/ocr/documents/' + row['id'], {'revision': 1, 'corrected_text': '新稿', 'reviewed': True}).status_code == 409


def test_source_changed_during_call_cannot_publish_success(client, monkeypatch):
    login(client)
    nid = image_book(client)
    row = create(client, request(client, nid)).json()
    def changed(request, count):
        import httpx
        from test_ai_chat import stream_bytes
        with SessionLocal.begin() as db:
            db.get(File, UUID(row['file_id'])).sha256 = 'c' * 64
        return httpx.Response(200, content=stream_bytes('迟到识别结果'))
    calls = mock(monkeypatch, changed)
    assert chat_service.tick() and len(calls) == 1
    assert read(client, row)['status'] != 'succeeded'
    assert not chat_service.tick() and len(calls) == 1


def test_rename_reorder_keeps_identity_and_draft(client, monkeypatch):
    login(client)
    nid = image_book(client)
    body = request(client, nid)
    row = create(client, body).json()
    mock(monkeypatch, answer='原文')
    chat_service.tick()
    with SessionLocal.begin() as db:
        note = db.get(Note, UUID(nid))
        note.title = '改标题'; note.revision += 1
        db.get(NotePage, UUID(body['page_id'])).revision += 1
    assert read(client, row)['source_valid']
    assert mutate(client, 'PATCH', '/ocr/documents/' + row['id'], {'revision': 1, 'corrected_text': '', 'reviewed': True}).status_code == 200


def test_activity_configuration_and_ambiguous_failure_no_retry(client, monkeypatch):
    login(client)
    nid = image_book(client)
    body = request(client, nid)
    monkeypatch.setattr(settings(), 'deepseek_api_key', type(settings().deepseek_api_key)(''))
    assert create(client, body).status_code == 503
    monkeypatch.setattr(settings(), 'deepseek_api_key', type(settings().deepseek_api_key)('fake-test'))
    row = create(client, body).json()
    assert create(client, {**body, 'request_key': str(uuid4())}).status_code == 409
    with SessionLocal.begin() as db:
        run = db.get(AiRun, UUID(row['run_id']))
        run.status = 'running'; run.lease_until = utcnow() - timedelta(seconds=1)
    assert not chat_service.tick()
    assert read(client, row)['status'] == 'interrupted'
    assert not chat_service.tick()


def test_cross_account_admin_has_no_private_ocr_access(client, monkeypatch):
    login(client)
    nid = image_book(client)
    body = request(client, nid)
    row = create(client, body).json()
    with SessionLocal.begin() as db:
        db.add(User(username='other_admin', display_name='另一管理员', role='admin', password_hash=hasher.hash('other-admin-password')))
    login(client, 'other_admin', 'other-admin-password')
    assert client.get('/api/v1/ocr/documents/' + row['id']).status_code == 404
    assert client.get('/api/v1/ocr/documents', params={'note_id': nid}).status_code == 404
    assert create(client, {**body, 'request_key': str(uuid4())}).status_code == 404
    assert mutate(client, 'PATCH', '/ocr/documents/' + row['id'], {'revision': 1, 'corrected_text': '越权', 'reviewed': True}).status_code == 404


@pytest.mark.parametrize('failure', ['http_error', 'truncated', 'empty'])
def test_provider_terminal_failure_is_not_transcript_or_automatic_retry(client, monkeypatch, failure):
    import httpx
    from test_ai_chat import stream_bytes
    login(client)
    nid = image_book(client)
    row = create(client, request(client, nid)).json()
    def handle(request, count):
        if failure == 'http_error':
            return httpx.Response(429)
        if failure == 'truncated':
            return httpx.Response(200, content=stream_bytes('未完成转录', reason='length'))
        return httpx.Response(200, content=stream_bytes(''))
    calls = mock(monkeypatch, handle)
    assert chat_service.tick()
    result = read(client, row)
    assert result['status'] in {'failed', 'interrupted', 'truncated'}
    assert result['raw_text'] is None and len(calls) == 1
    assert mutate(client, 'PATCH', '/ocr/documents/' + row['id'], {'revision': 1, 'corrected_text': '假成功', 'reviewed': True}).status_code == 409
    for _ in range(2):
        assert not chat_service.tick()
        assert read(client, row)['raw_text'] is None
    assert len(calls) == 1


def test_chat_and_ocr_share_active_run_limit(client, monkeypatch):
    from test_ai_chat import conversation, submit
    login(client)
    nid = image_book(client)
    cid = conversation(client)
    text_run = submit(client, cid, '独立文字问题')
    assert text_run.status_code == 201, text_run.text
    assert create(client, request(client, nid)).status_code == 409
    with SessionLocal.begin() as db:
        db.get(AiRun, UUID(text_run.json()['id'])).status = 'cancelled'
    ocr_run = create(client, request(client, nid))
    assert ocr_run.status_code == 201
    assert submit(client, cid, 'OCR忙碌时不能绕过', previous_run_id=text_run.json()['id']).status_code == 409


def test_ocr_frequency_limit_rolls_back_internal_conversation(client):
    login(client)
    nid = image_book(client)
    body = request(client, nid)
    with SessionLocal.begin() as db:
        owner = db.scalar(select(User).where(User.username == 'admin'))
        parent = AiConversation(owner_id=owner.id, request_key=uuid4(), title='近期用量')
        db.add(parent); db.flush()
        for _ in range(30):
            db.add(AiRun(owner_id=owner.id, conversation_id=parent.id, request_key=uuid4(),
                         request_hash='a'*64, model='deepseek-flash', prompt='合成', input_messages=[], status='failed'))
    assert create(client, body).status_code == 429
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AiConversation)) == 1


def test_source_change_keeps_received_usage_without_publishing_or_replay(client, monkeypatch):
    from app.modules.ai.models import AiStep
    login(client)
    nid = image_book(client)
    row = create(client, request(client, nid)).json()
    usage = {'prompt_tokens': 123, 'completion_tokens': 12, 'total_tokens': 135}
    calls = []
    def stream(secret, model, messages, **kwargs):
        calls.append(1)
        yield {'delta': '已识别部分'}
        with SessionLocal.begin() as db:
            db.get(File, UUID(row['file_id'])).sha256 = 'd' * 64
        yield {'usage': usage, 'finish': 'stop'}
    monkeypatch.setattr(chat_provider, 'stream_chat', stream)
    assert chat_service.tick()
    done = read(client, row)
    assert not done['source_valid'] and done['status'] != 'succeeded' and done['raw_text'] is None
    assert done['usage'] == usage
    with SessionLocal() as db:
        assert db.scalar(select(AiStep).where(AiStep.run_id == UUID(row['run_id']))).usage == usage
    assert not chat_service.tick() and len(calls) == 1
