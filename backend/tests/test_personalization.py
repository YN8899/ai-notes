"""Real HTTP/SQL integration tests. Run ONLY with the exclusive isolated DB lease."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.modules.ai import chat_provider, chat_service, compatible, tool_runner
from app.modules.ai.models import AiConversation, AiRun
from app.modules.community.models import CommunityPost
from app.modules.personalization import recommendations
from app.modules.personalization.models import LearningAnalysis, LearningAnalysisPreview
from app.shared.db import SessionLocal, utcnow
from app.shared.errors import AppError
from app.shared.models import AuthSession, User
from conftest import login, mutate
from test_community import published
from test_social import people, create_note
from test_archive_restore import restore_tool, restore_database, target, _restore, _metadata, _rewrite_archive

BASE = '/api/v1/personalization'


def preferences(client):
    response = client.get(BASE + '/preferences')
    assert response.status_code == 200, response.text
    return response.json()


def save(client, **changes):
    current = preferences(client)
    current.pop('updated_at')
    return mutate(client, 'PUT', '/personalization/preferences', {**current, **changes})


@pytest.fixture
def model(monkeypatch):
    # Synthetic in-process metadata, no external config, resolver DNS or model POST.
    selected = SimpleNamespace(provider='synthetic', model='local-test', fingerprint='a'*64,
        capabilities={'text': True, 'vision': False, 'tools': False, 'streaming': True},
        endpoint='https://unused.invalid/chat/completions', secret='synthetic-test-secret')
    def resolve(provider, model, **kwargs):
        assert provider == 'synthetic' and model == 'local-test' and not kwargs.get('tools')
        return selected
    monkeypatch.setattr(compatible, 'resolve', resolve)
    return selected


def preview(client):
    today = utcnow().date().isoformat()
    response = mutate(client, 'POST', '/personalization/analysis/preview', {
        'date_from': today, 'date_to': today, 'provider': 'synthetic', 'model': 'local-test'})
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client, prepared=None, key=None):
    prepared = prepared or preview(client)
    body = {'preview_id': prepared['id'], 'preview_hash': prepared['preview_hash'], 'request_key': str(key or uuid4())}
    return mutate(client, 'POST', '/personalization/analysis', body), body


def test_preferences_defaults_persist_conflict_reset_and_owner_boundary(people, client):
    clients, ids = people
    alice, bob = clients['alice'], clients['bob']
    initial = preferences(alice)
    assert initial['revision'] == 0 and initial['default_section'] == 'home'
    assert not initial['recommendations_enabled'] and not initial['ai_analysis_enabled']
    changed = save(alice, tile_order=list(reversed(initial['tile_order'])), hidden_tiles=['social'], default_section='reviews')
    assert changed.status_code == 200 and changed.json()['revision'] == 1
    assert preferences(bob)['revision'] == 0
    login(client)
    assert preferences(client)['revision'] == 0
    conflict = save(alice, revision=0, hidden_tiles=['notes'])
    assert conflict.status_code == 409 and conflict.json()['code'] == 'home_preferences_changed'
    assert preferences(alice)['hidden_tiles'] == ['social']
    assert save(alice, owner_id=ids['bob']).status_code == 422
    assert save(alice, default_section='audit').status_code == 422
    restored = mutate(alice, 'POST', '/personalization/preferences/reset', {'revision': 1})
    assert restored.status_code == 200 and restored.json()['tile_order'] == initial['tile_order']
    assert restored.json()['hidden_tiles'] == [] and restored.json()['revision'] == 2


@pytest.mark.parametrize('changes', [{'tile_order': ['notes']*5}, {'hidden_tiles': ['notes','notes']},
    {'ai_analysis_enabled': 'true'}, {'recommendations_enabled': 1}, {'revision': True},
    {'default_section': 'users'}, {'subjects': ['数学','数学']}, {'tags': [' x']}])
def test_preferences_strict_api_validation(people, changes):
    assert save(people[0]['alice'], **changes).status_code == 422


def test_disabled_recommendations_never_query_learning_signals_and_latest_paginates(people, monkeypatch):
    alice = people[0]['alice']
    for i in range(3):
        published(alice, title='published-' + str(i))
    monkeypatch.setattr(recommendations, 'signals', lambda *args: (_ for _ in ()).throw(AssertionError('private signals consulted')))
    first = alice.get(BASE + '/recommendations?limit=1').json()
    assert first['mode'] == 'latest' and first['reasons_used'] == [] and first['next_cursor']
    second = alice.get(BASE + '/recommendations', params={'limit': 1, 'cursor': first['next_cursor']}).json()
    assert first['items'][0]['id'] != second['items'][0]['id']


def test_personal_rank_explanations_cursor_reset_and_public_visibility(people, client):
    clients, ids = people
    alice, bob = clients['alice'], clients['bob']
    match = published(bob, title='math', subject='数学', tags=['代数'])
    for i in range(3):
        published(bob, title='other-' + str(i), subject='历史', tags=[])
    assert save(alice, recommendations_enabled=True, subjects=['数学'], tags=['代数']).status_code == 200
    first = alice.get(BASE + '/recommendations?limit=1').json()
    assert first['items'][0]['id'] == match['id'] and '本人手动学科兴趣：数学' in first['items'][0]['recommendation_reason']
    second = alice.get(BASE + '/recommendations', params={'limit': 1, 'cursor': first['next_cursor']})
    assert second.status_code == 200 and second.json()['items'][0]['id'] != match['id']
    assert save(alice, subjects=['历史']).status_code == 200
    reset = alice.get(BASE + '/recommendations', params={'cursor': first['next_cursor']})
    assert reset.status_code == 409 and reset.json()['code'] == 'recommendations_reset_required'
    with SessionLocal.begin() as db:
        db.get(User, UUID(ids['bob'])).status = 'disabled'
    assert alice.get(BASE + '/recommendations').json()['items'] == []


def test_withdrawal_and_hidden_snapshot_disappear_immediately(people):
    alice, bob = people[0]['alice'], people[0]['bob']
    one, two = published(bob, title='one'), published(bob, title='two')
    save(alice, recommendations_enabled=True)
    with SessionLocal.begin() as db:
        db.get(CommunityPost, UUID(one['id'])).status = 'withdrawn'
        db.get(CommunityPost, UUID(two['id'])).status = 'hidden'
    assert alice.get(BASE + '/recommendations').json()['items'] == []


def test_personal_signals_use_only_owned_tags_and_change_resets_paging(people):
    from app.modules.learning.event_models import LearningEvent
    from app.modules.notes.models import NoteTag, Tag
    clients, ids = people
    alice, bob = clients['alice'], clients['bob']
    own = create_note(alice, title='PRIVATE_ALICE_TITLE')
    foreign = create_note(bob, title='PRIVATE_BOB_TITLE')
    now = utcnow()
    with SessionLocal.begin() as db:
        for name, note, tagname in (('alice', own, '本人标签'), ('bob', foreign, '他人私密标签')):
            owner = UUID(ids[name])
            tag = Tag(owner_id=owner, name=tagname)
            db.add(tag)
            db.flush()
            db.add(NoteTag(owner_id=owner, note_id=UUID(note['id']), tag_id=tag.id))
            db.add(LearningEvent(owner_id=owner, event_id=uuid4(), kind='reading', note_id=UUID(note['id']),
                started_at=now-timedelta(seconds=15), ended_at=now-timedelta(seconds=10), request_hash='a'*64))
    match = published(bob, title='匹配公开内容', tags=['本人标签'])
    published(bob, title='不匹配公开内容', tags=[])
    save(alice, recommendations_enabled=True, learning_signals_enabled=True)
    first = alice.get(BASE + '/recommendations?limit=1').json()
    assert first['items'][0]['id'] == match['id']
    assert '本人近30天最多200条活动' in first['items'][0]['recommendation_reason']
    assert 'PRIVATE_ALICE_TITLE' not in str(first) and 'PRIVATE_BOB_TITLE' not in str(first)
    assert '他人私密标签' not in str(first)
    with SessionLocal.begin() as db:
        db.add(LearningEvent(owner_id=UUID(ids['alice']), event_id=uuid4(), kind='reading', note_id=UUID(own['id']),
            started_at=now-timedelta(seconds=25), ended_at=now-timedelta(seconds=20), request_hash='b'*64))
    reset = alice.get(BASE + '/recommendations', params={'cursor': first['next_cursor']})
    assert reset.status_code == 409 and reset.json()['code'] == 'recommendations_reset_required'


def test_facts_work_with_ai_disabled_and_keep_sample_caveat(people):
    alice = people[0]['alice']
    result = alice.get(BASE + '/analysis/facts')
    assert result.status_code == 200
    assert result.json()['summary']['sample']['insufficient'] is True
    assert result.json()['summary']['interpretation'] == 'descriptive_facts_not_mastery'
    assert any(rule.get('rule') == 'reading_not_mastery' for rule in result.json()['rules'])


def test_explicit_preview_freezes_only_summary_and_create_is_atomic_idempotent(people, model):
    alice, bob = people[0]['alice'], people[0]['bob']
    today = utcnow().date().isoformat()
    disabled = mutate(alice, 'POST', '/personalization/analysis/preview', {'date_from': today, 'date_to': today,
        'provider': 'synthetic', 'model': 'local-test'})
    assert disabled.status_code == 409
    assert save(alice, ai_analysis_enabled=True).status_code == 200
    prepared = preview(alice)
    assert prepared['planned_calls'] == 1 and prepared['tools'] == []
    assert 'title' not in str(prepared['summary']) and 'owner_id' not in str(prepared['summary'])
    assert prepared['input_bytes'] == sum(len(item['content'].encode()) for item in prepared['input_messages'])
    foreign, body = confirm(bob, prepared)
    assert foreign.status_code == 409
    created, body = confirm(alice, prepared)
    assert created.status_code == 201 and created.json()['status'] == 'queued'
    duplicate = mutate(alice, 'POST', '/personalization/analysis', body)
    assert duplicate.status_code == 201 and duplicate.json()['id'] == created.json()['id']
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(LearningAnalysis)) == 1
        assert db.scalar(select(func.count()).select_from(LearningAnalysisPreview)) == 0
        run = db.get(AiRun, UUID(created.json()['run_id']))
        assert run.tools == [] and run.tool_scope is None and run.source is None and run.auth_session_id
        assert run.input_messages == prepared['input_messages']
    assert bob.get(BASE + '/analysis/' + created.json()['id']).status_code == 404
    assert mutate(bob, 'POST', '/personalization/analysis/' + created.json()['id'] + '/cancel', {}).status_code == 404


def test_preview_expiry_preference_change_and_hash_tamper_are_refused(people, model):
    alice = people[0]['alice']
    save(alice, ai_analysis_enabled=True)
    prepared = preview(alice)
    with SessionLocal.begin() as db:
        db.get(LearningAnalysisPreview, UUID(prepared['id'])).expires_at = utcnow() - timedelta(seconds=1)
    assert confirm(alice, prepared)[0].status_code == 409
    prepared = preview(alice)
    save(alice, hidden_tiles=['social'])
    assert confirm(alice, prepared)[0].status_code == 409
    prepared = preview(alice)
    prepared['preview_hash'] = 'f'*64
    assert confirm(alice, prepared)[0].status_code == 409


def test_disable_cancels_before_paid_post_and_history_survives_provider_removal(people, model, monkeypatch):
    alice = people[0]['alice']
    save(alice, ai_analysis_enabled=True)
    created, _ = confirm(alice)
    assert created.status_code == 201
    run_id = UUID(created.json()['run_id'])
    assert chat_service.claim() == run_id
    save(alice, ai_analysis_enabled=False)
    assert tool_runner.before_send(run_id) is False
    monkeypatch.setattr(compatible, 'resolve', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('history read catalog')))
    history = alice.get(BASE + '/analysis/' + created.json()['id'])
    assert history.status_code == 200 and history.json()['status'] == 'cancelled'


def test_origin_session_revocation_and_tampered_wire_fail_final_gate(people, model):
    alice = people[0]['alice']
    save(alice, ai_analysis_enabled=True)
    created, _ = confirm(alice)
    run_id = UUID(created.json()['run_id'])
    assert chat_service.claim() == run_id
    with SessionLocal.begin() as db:
        run = db.get(AiRun, run_id)
        run.input_messages = [{'role': 'user', 'content': 'private raw data'}]
    with pytest.raises(AppError) as failure:
        tool_runner.before_send(run_id)
    assert failure.value.code == 'analysis_scope_changed'
    with SessionLocal.begin() as db:
        run = db.get(AiRun, run_id)
        db.get(AuthSession, run.auth_session_id).revoked_at = utcnow()
    with pytest.raises(chat_provider.ProviderFailure) as failure:
        tool_runner.before_send(run_id)
    assert failure.value.code == 'session_expired'


def test_worker_uses_normal_status_usage_and_reserved_conversation(people, model, monkeypatch):
    alice = people[0]['alice']
    save(alice, ai_analysis_enabled=True)
    created, _ = confirm(alice)
    run_id = UUID(created.json()['run_id'])
    with SessionLocal() as db:
        conversation_id = str(db.get(AiRun, run_id).conversation_id)
    assert alice.get('/api/v1/ai/conversations/' + conversation_id).status_code == 404
    assert not any(row['id'] == conversation_id for row in alice.get('/api/v1/ai/conversations').json())
    def synthetic_stream(*args, **kwargs):
        assert kwargs['before_send']() is True and not kwargs.get('tools')
        yield {'delta': '仅解释已记录事实；阅读不代表掌握。', 'finish': 'stop',
               'usage': {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}}
    monkeypatch.setattr(chat_provider, 'stream_chat', synthetic_stream)
    assert chat_service.tick()
    final = alice.get(BASE + '/analysis/' + created.json()['id']).json()
    assert final['status'] == 'succeeded' and final['usage']['total_tokens'] == 20
    assert final['answer'] == '仅解释已记录事实；阅读不代表掌握。'


def test_v5_preferences_analysis_roundtrip_has_no_preview_or_replay_authority(
    people, model, restore_tool, target, tmp_path, monkeypatch,
):
    alice = people[0]['alice']
    save(alice, ai_analysis_enabled=True, recommendations_enabled=True, subjects=['数学'], hidden_tiles=['messages'])
    created, _ = confirm(alice)
    assert created.status_code == 201
    assert mutate(alice, 'POST', '/personalization/analysis/' + created.json()['id'] + '/cancel', {}).status_code == 200
    preview(alice)  # Technical consent preparation must not be in the account ZIP.
    response = mutate(alice, 'POST', '/exports/archive', {})
    assert response.status_code == 200, response.text
    path = tmp_path / 'personalization-v5.zip'
    path.write_bytes(response.content)
    manifest, rows = _metadata(path)
    assert manifest['version'] == 5 and manifest['counts']['user_home_preferences'] == 1
    assert manifest['counts']['learning_analyses'] == 1 and 'learning_analysis_previews' not in rows
    assert rows['user_home_preferences'][0]['preferences']['ai_analysis_enabled'] is True
    frozen = rows['learning_analyses'][0]
    assert 'auth_session_id' not in frozen and frozen['snapshot']['input_messages']
    assert restore_tool._load_verifier().verify_archive(path) == manifest['counts']
    _restore(restore_tool, path, target)
    from app.modules.personalization.models import UserHomePreferences
    from app.modules.personalization import analysis_service
    with target.sessions() as db:
        owner = UUID(people[1]['alice'])
        preferences = db.get(UserHomePreferences, owner).preferences
        assert not preferences['ai_analysis_enabled'] and preferences['recommendations_enabled']
        assert preferences['subjects'] == ['数学'] and preferences['hidden_tiles'] == ['messages']
        assert db.scalar(select(func.count()).select_from(LearningAnalysisPreview)) == 0
        run = db.get(AiRun, UUID(created.json()['run_id']))
        assert run.status == 'cancelled' and run.auth_session_id is None and run.input_messages == []
        assert analysis_service.output(db, owner, UUID(created.json()['id'])).summary == created.json()['summary']
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
    # A freshly created synthetic login authorizes export; the restore itself
    # created no session and cannot resume the historical run.
    from app.modules.exports import service as exports
    from app.shared.config import settings
    import shutil
    with target.sessions.begin() as db:
        session = AuthSession(user_id=owner, token_hash='a'*64, csrf_hash='b'*64,
                              expires_at=utcnow()+timedelta(hours=1))
        db.add(session)
        db.flush()
        session_id = session.id
    monkeypatch.setattr(exports, 'SessionLocal', target.sessions)
    monkeypatch.setattr(settings(), 'storage_root', str(target.storage_root))
    reexport = tmp_path / 'personalization-reexport.zip'
    with exports.create_archive(owner, session_id) as stream, reexport.open('wb') as destination:
        shutil.copyfileobj(stream, destination)
    remanifest, rerows = _metadata(reexport)
    assert restore_tool._load_verifier().verify_archive(reexport) == remanifest['counts']
    assert rerows['learning_analyses'] == rows['learning_analyses']
    assert not rerows['user_home_preferences'][0]['preferences']['ai_analysis_enabled']
    bad = tmp_path / 'personalization-tampered.zip'
    def tamper(manifest, rows):
        rows['user_home_preferences'][0]['preferences']['default_section'] = 'audit'
    _rewrite_archive(path, bad, tamper)
    verifier = restore_tool._load_verifier()
    with pytest.raises(verifier.ValidationError):
        verifier.verify_archive(bad)
    huge = tmp_path / 'personalization-huge-integer.zip'
    def huge_integer(manifest, rows):
        rows['learning_analyses'][0]['snapshot']['summary']['facts']['activity']['total_seconds'] = 10**1000
    _rewrite_archive(path, huge, huge_integer)
    with pytest.raises(verifier.ValidationError):
        verifier.verify_archive(huge)
