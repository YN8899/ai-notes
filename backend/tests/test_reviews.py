"""Real-PG review transactions; no live AI calls or source-progress coupling."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Barrier
from uuid import UUID, uuid4
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, DBAPIError
from app.modules.learning import review_service as service, review_schemas as s, scheduler
from app.modules.learning.models import Card, Deck
from app.modules.learning.quiz_models import QuizIdentity
from app.modules.learning.review_models import ReviewItem, ReviewLog, ReviewPlan
from app.modules.notes.models import Note
from app.shared.db import SessionLocal, utcnow
from app.shared.errors import AppError
from app.shared.models import User
from app.shared.security import hasher
from conftest import login, mutate
from test_cards import deck, create_card
from test_text_selection import note as text_note


def create_note_plan(client, **overrides):
    n, _ = text_note(client)
    data = dict(kind='note', note_id=n['id'], request_key=str(uuid4()), **overrides)
    result = mutate(client, 'POST', '/learning/review-plans', data)
    assert result.status_code == 201, result.text
    return n, result.json()['plan'], data


def patch(client, plan, operation, **fields):
    return mutate(client, 'PATCH', '/learning/review-plans/' + plan['id'],
                  dict(request_key=str(uuid4()), revision=plan['revision'], operation=operation, **fields))


def review(client, plan, **fields):
    body = {'request_key': str(uuid4()), 'revision': plan['revision'], 'rating': 3, **fields}
    if plan['strategy'] == 'manual':
        body.setdefault('next_due_at', (utcnow() + timedelta(days=2)).isoformat())
        body.setdefault('reason', '自主安排')
    return mutate(client, 'POST', '/learning/review-plans/' + plan['id'] + '/reviews', body)


def read(client, plan):
    result = client.get('/api/v1/learning/review-plans/' + plan['id'])
    assert result.status_code == 200, result.text
    return result.json()


def test_create_practice_replay_and_independent_objects(client, monkeypatch):
    login(client)
    n, note_plan, data = create_note_plan(client)
    replay = mutate(client, 'POST', '/learning/review-plans', data)
    assert replay.json()['plan'] == note_plan
    assert mutate(client, 'POST', '/learning/review-plans', {**data, 'strategy': 'fsrs'}).status_code == 409
    assert mutate(client, 'POST', '/learning/review-plans', {**data, 'request_key': str(uuid4())}).status_code == 409
    d = deck(client)
    card = create_card(client, d['id'], status='confirmed').json()
    plans = client.get('/api/v1/learning/review-plans').json()
    card_plan = next(p for p in plans['items'] if p['item']['card_id'] == card['id'])
    assert plans['total'] == 2 and card_plan['id'] != note_plan['id']
    with SessionLocal.begin() as db:
        owner = db.scalar(select(User.id).where(User.username == 'admin'))
        q = QuizIdentity(owner_id=owner, title='测验', status='confirmed', question_count=1)
        db.add(q)
        db.flush()
        qid = str(q.id)
    result = mutate(client, 'POST', '/learning/review-plans', dict(kind='quiz', quiz_id=qid, request_key=str(uuid4())))
    assert result.status_code == 201, result.text
    quiz_plan = result.json()['plan']
    def forbidden(*args, **kwargs):
        raise AssertionError('Practice cannot run scheduler')
    monkeypatch.setattr(scheduler, 'schedule', forbidden)
    practice_body = dict(kind='card', card_id=card['id'], plan_id=card_plan['id'], revision=1, rating=2, request_key=str(uuid4()))
    done = mutate(client, 'POST', '/learning/review-items/practice', practice_body)
    assert done.status_code == 200, done.text
    assert done.json()['log']['old_snapshot'] == done.json()['log']['new_snapshot']
    assert mutate(client, 'POST', '/learning/review-items/practice', practice_body).json() == done.json()
    assert read(client, card_plan) == card_plan
    assert read(client, note_plan) == note_plan and read(client, quiz_plan) == quiz_plan
    logs = client.get('/api/v1/learning/review-logs').json()
    assert logs['total'] == 4


def test_practice_without_plan_does_not_create_plan(client):
    login(client)
    n, _ = text_note(client)
    data = dict(kind='note', note_id=n['id'], rating=4, request_key=str(uuid4()))
    result = mutate(client, 'POST', '/learning/review-items/practice', data)
    assert result.status_code == 200 and result.json()['plan'] is None
    assert result.json()['log']['plan_id'] is None
    assert client.get('/api/v1/learning/review-plans').json()['total'] == 0


def test_card_confirm_draft_archive_deck_and_explicit_resume(client):
    login(client)
    d = deck(client)
    c = create_card(client, d['id']).json()
    assert client.get('/api/v1/learning/review-due').json()['total'] == 0
    bad = dict(kind='card', card_id=c['id'], request_key=str(uuid4()))
    assert mutate(client, 'POST', '/learning/review-plans', bad).status_code == 409
    c = mutate(client, 'PATCH', '/learning/cards/' + c['id'], dict(revision=1, status='confirmed')).json()
    p = client.get('/api/v1/learning/review-due').json()['items'][0]
    assert p['strategy'] == 'manual'
    changed = review(client, p).json()['plan']
    c = mutate(client, 'PATCH', '/learning/cards/' + c['id'], dict(revision=2, front='新题面', status='confirmed')).json()
    assert read(client, p)['revision'] == changed['revision']
    c = mutate(client, 'PATCH', '/learning/cards/' + c['id'], dict(revision=3, status='draft')).json()
    paused = read(client, p)
    assert paused['status'] == 'paused'
    assert patch(client, paused, 'resume').status_code == 409
    c = mutate(client, 'PATCH', '/learning/cards/' + c['id'], dict(revision=4, status='confirmed')).json()
    assert read(client, p)['status'] == 'paused'
    resumed = patch(client, paused, 'resume').json()['plan']
    assert resumed['status'] == 'active'
    assert mutate(client, 'PATCH', '/learning/decks/' + d['id'], dict(revision=1, status='archived')).status_code == 200
    paused = read(client, p)
    assert paused['status'] == 'paused' and not paused['item']['eligible']
    assert client.get('/api/v1/learning/review-due').json()['total'] == 0
    assert mutate(client, 'PATCH', '/learning/decks/' + d['id'], dict(revision=2, status='active')).status_code == 200
    assert read(client, p)['status'] == 'paused'
    assert patch(client, paused, 'resume').status_code == 200
    assert mutate(client, 'PATCH', '/learning/cards/' + c['id'], dict(revision=5, status='archived')).status_code == 200
    assert read(client, p)['status'] == 'paused'
    facts = client.get('/api/v1/learning/review-logs', params={'plan_id': p['id']}).json()['items']
    assert {'card_draft', 'deck_archived', 'card_archived'} <= {l['reason'] for l in facts}


def test_note_recycle_hides_own_plan_but_retains_independent_card(client):
    login(client)
    n, p, _ = create_note_plan(client)
    d = deck(client)
    c = create_card(client, d['id'], status='confirmed').json()
    with SessionLocal.begin() as db:
        db.get(Note, UUID(n['id'])).deleted_at = utcnow()
    assert not read(client, p)['item']['eligible']
    assert review(client, p).status_code == 409
    items = client.get('/api/v1/learning/review-due').json()['items']
    assert len(items) == 1 and items[0]['item']['card_id'] == c['id']
    with SessionLocal.begin() as db:
        db.get(Note, UUID(n['id'])).deleted_at = None
    assert client.get('/api/v1/learning/review-due').json()['total'] == 2


@pytest.mark.parametrize('status,count', [('draft', 1), ('confirmed', 0), ('archived', 1)])
def test_unusable_quiz_cannot_plan_or_practice(client, status, count):
    login(client)
    with SessionLocal.begin() as db:
        owner = db.scalar(select(User.id).where(User.username == 'admin'))
        q = QuizIdentity(owner_id=owner, title='无效测验', status=status, question_count=count)
        db.add(q)
        db.flush()
        qid = str(q.id)
    data = dict(kind='quiz', quiz_id=qid, request_key=str(uuid4()))
    assert mutate(client, 'POST', '/learning/review-plans', data).status_code == 409
    assert mutate(client, 'POST', '/learning/review-items/practice', {**data, 'rating': 3}).status_code == 409


def test_manual_review_logs_server_time_and_retry_original_receipt(client):
    login(client)
    _, p, _ = create_note_plan(client)
    key = str(uuid4())
    due = (utcnow() + timedelta(days=4)).isoformat()
    result = review(client, p, request_key=key, next_due_at=due)
    assert result.status_code == 200, result.text
    first = result.json()
    log = first['log']
    assert log['old_snapshot']['revision'] == 1 and log['new_snapshot']['revision'] == 2
    assert log['scheduled_for'] == p['next_due_at']
    assert first['plan']['next_due_at'] != p['next_due_at']
    assert review(client, first['plan']).status_code == 200
    assert review(client, p, request_key=key, next_due_at=due).json() == first
    assert review(client, p, request_key=key, next_due_at=due, rating=1).status_code == 409
    assert review(client, p).status_code == 409
    assert client.get('/api/v1/learning/review-logs', params={'action': 'review'}).json()['total'] == 2


@pytest.mark.parametrize('rating', [1, 2, 3, 4])
def test_fsrs_matches_official_each_rating_and_durable_roundtrip(client, rating):
    from fsrs import Card as FsrsCard, Rating, Scheduler
    login(client)
    _, p, _ = create_note_plan(client, strategy='fsrs')
    old = deepcopy(p['scheduler_state'])
    done = review(client, p, rating=rating)
    assert done.status_code == 200, done.text
    result = done.json()
    when = result['log']['reviewed_at']
    from datetime import datetime
    expected, _ = Scheduler(enable_fuzzing=False).review_card(FsrsCard.from_dict(old['card']), Rating(rating), review_datetime=datetime.fromisoformat(when))
    actual = read(client, p)
    assert actual['scheduler_state']['card'] == expected.to_dict()
    assert actual['algorithm_version'] == scheduler.FSRS_VERSION
    assert actual['next_due_at'] == expected.due.isoformat().replace('+00:00', 'Z')


def test_strategy_switch_history_resume_restart_and_manual_reschedule(client):
    login(client)
    _, p, _ = create_note_plan(client, strategy='fsrs')
    p = review(client, p).json()['plan']
    learned = deepcopy(p['scheduler_state'])
    p = patch(client, p, 'switch', strategy='manual', reason='改为自主排期').json()['plan']
    assert p['last_fsrs_state']['scheduler_state'] == learned and p['scheduler_state'] == {}
    retained = deepcopy(p['last_fsrs_state'])
    p = review(client, p).json()['plan']
    assert p['last_fsrs_state'] == retained
    p = patch(client, p, 'reschedule', next_due_at=utcnow().isoformat(), reason='现在复习').json()['plan']
    assert p['last_fsrs_state'] == retained
    p = patch(client, p, 'switch', strategy='fsrs', fsrs_mode='resume_last', reason='恢复原策略').json()['plan']
    assert p['scheduler_state'] == learned
    p = patch(client, p, 'switch', strategy='manual', reason='临时').json()['plan']
    p = patch(client, p, 'switch', strategy='fsrs', fsrs_mode='restart', reason='重新开始').json()['plan']
    assert p['scheduler_state']['card']['last_review'] is None
    history = client.get('/api/v1/learning/review-logs', params={'plan_id': p['id']}).json()['items']
    assert len(history) == 8
    assert any(l['old_snapshot'] and l['old_snapshot']['scheduler_state'] == learned for l in history)


def test_contract_naive_null_reason_limits_and_corrupt_state(client):
    login(client)
    _, p, _ = create_note_plan(client)
    for due in [None, '2026-11-01T12:00:00', (utcnow() + timedelta(days=36501)).isoformat()]:
        assert review(client, p, next_due_at=due).status_code == 422
    assert review(client, p, reason=None).status_code == 422
    assert review(client, p, reviewed_at=utcnow().isoformat()).status_code == 422
    assert review(client, p, rating=True).status_code == 422
    assert patch(client, p, 'reschedule', next_due_at=utcnow().isoformat()).status_code == 422
    assert patch(client, p, 'switch', strategy='fsrs', reason='缺选择').status_code == 422
    assert client.get('/api/v1/learning/review-due', params={'limit': 51}).status_code == 422
    assert client.get('/api/v1/learning/review-plans', params={'due_from': '2026-11-01T00:00:00'}).status_code == 422
    p = patch(client, p, 'switch', strategy='fsrs', fsrs_mode='restart', reason='测试').json()['plan']
    with SessionLocal.begin() as db:
        db.get(ReviewPlan, UUID(p['id'])).scheduler_state = {'card': {'invalid': True}}
    assert review(client, p).status_code == 409
    with SessionLocal.begin() as db:
        row = db.get(ReviewPlan, UUID(p['id']))
        row.scheduler_state = p['scheduler_state']
        row.algorithm_version = 'unknown/999'
    assert patch(client, p, 'switch', strategy='manual', reason='不能静默绕过未知版本').status_code == 409


def test_foreign_owner_unknown_ids_csrf_and_plan_item_match(client):
    login(client)
    n, p, _ = create_note_plan(client)
    assert client.post('/api/v1/learning/review-items/practice', json={}).status_code == 403
    with SessionLocal.begin() as db:
        db.add(User(username='review-other', display_name='other', password_hash=hasher.hash('member-password')))
    login(client, 'review-other', 'member-password')
    assert client.get('/api/v1/learning/review-plans/' + p['id']).status_code == 404
    assert patch(client, p, 'pause').status_code == 404
    assert review(client, p).status_code == 404
    assert client.get('/api/v1/learning/review-logs', params={'plan_id': p['id']}).status_code == 404
    assert mutate(client, 'POST', '/learning/review-plans', dict(kind='note', note_id=n['id'], request_key=str(uuid4()))).status_code == 404
    assert client.get('/api/v1/learning/review-plans').json()['total'] == 0
    assert client.get('/api/v1/learning/review-due').json()['total'] == 0
    assert client.get('/api/v1/learning/review-logs').json()['total'] == 0


@pytest.mark.parametrize('strategy,version,state', [
    ('manual', 'manual/999', {'future_schema': {'x': 1}}),
    ('manual', scheduler.MANUAL_VERSION, {'unknown_state': 1}),
    ('fsrs', 'fsrs/999', None),
])
def test_switch_rejects_unknown_current_version_or_state_without_any_change(client, strategy, version, state):
    login(client)
    _, p, _ = create_note_plan(client, strategy=strategy)
    with SessionLocal.begin() as db:
        row = db.get(ReviewPlan, UUID(p['id']))
        row.algorithm_version = version
        if state is not None:
            row.scheduler_state = state
    before = read(client, p)
    logs_before = client.get('/api/v1/learning/review-logs').json()
    target = dict(strategy='fsrs', fsrs_mode='restart') if strategy == 'manual' else dict(strategy='manual')
    result = patch(client, before, 'switch', reason='拒绝未知原版本或原状态', **target)
    assert result.status_code == 409, result.text
    assert result.json()['code'] == 'scheduler_state_invalid'
    assert read(client, p) == before
    assert client.get('/api/v1/learning/review-logs').json() == logs_before


@pytest.mark.parametrize('same_key', [True, False])
def test_real_two_session_concurrent_reviews_exactly_once(client, same_key):
    login(client)
    _, p, _ = create_note_plan(client, strategy='fsrs')
    with SessionLocal() as db:
        owner = db.scalar(select(User.id).where(User.username == 'admin'))
    gate, key = Barrier(2), uuid4()
    def worker(index):
        with SessionLocal() as db:
            data = s.ReviewSubmit(request_key=key if same_key else uuid4(), revision=1, rating=3)
            gate.wait(timeout=10)
            try:
                return service.review(db, owner, UUID(p['id']), data).model_dump(mode='json')
            except AppError as exc:
                return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, range(2)))
    if same_key:
        assert results[0] == results[1]
    else:
        assert sum(isinstance(r, dict) for r in results) == 1
        assert 'revision_conflict' in results
    assert read(client, p)['revision'] == 2
    assert client.get('/api/v1/learning/review-logs', params={'action': 'review'}).json()['total'] == 1


def test_manual_formal_review_rejects_corrupt_state_without_changing_plan_or_logs(client):
    login(client)
    _, p, _ = create_note_plan(client)
    with SessionLocal.begin() as db:
        db.get(ReviewPlan, UUID(p['id'])).scheduler_state = {'unexpected': {'future': True}}
    before = read(client, p)
    logs_before = client.get('/api/v1/learning/review-logs').json()
    result = review(client, before)
    assert result.status_code == 409, result.text
    assert result.json()['code'] == 'scheduler_state_invalid'
    assert read(client, p) == before
    assert client.get('/api/v1/learning/review-logs').json() == logs_before


def test_real_constraints_owner_exact_source_partial_unique_and_log_append_only(client):
    login(client)
    n, p, _ = create_note_plan(client)
    with SessionLocal.begin() as db:
        owner = db.scalar(select(User.id).where(User.username == 'admin'))
        other = User(username='foreign', display_name='f', password_hash='fake')
        db.add(other)
        db.flush()
        foreign = other.id
    for kwargs in [dict(owner_id=foreign, kind='note', note_id=UUID(n['id'])),
                   dict(owner_id=owner, kind='card', note_id=UUID(n['id'])),
                   dict(owner_id=owner, kind='note')]:
        with SessionLocal() as db:
            db.add(ReviewItem(**kwargs))
            with pytest.raises(IntegrityError):
                db.flush()
    with SessionLocal() as db:
        old = db.get(ReviewPlan, UUID(p['id']))
        db.add(ReviewPlan(owner_id=owner, item_id=old.item_id, status='paused', strategy='manual', next_due_at=utcnow(), scheduler_state={}, algorithm_version='manual/1'))
        with pytest.raises(IntegrityError):
            db.flush()
    archived = patch(client, p, 'archive').json()['plan']
    new = mutate(client, 'POST', '/learning/review-plans', dict(kind='note', note_id=n['id'], request_key=str(uuid4())))
    assert new.status_code == 201 and new.json()['plan']['id'] != archived['id']
    with SessionLocal() as db:
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE review_logs SET reason='changed'"))


def test_practice_plan_mismatch_and_no_mutation_on_get(client):
    login(client)
    n, p, _ = create_note_plan(client)
    other, _, _ = create_note_plan(client)
    wrong = dict(kind='note', note_id=other['id'], plan_id=p['id'], revision=1, request_key=str(uuid4()), rating=3)
    assert mutate(client, 'POST', '/learning/review-items/practice', wrong).status_code == 404
    assert mutate(client, 'POST', '/learning/review-items/practice', {**wrong, 'note_id': n['id'], 'revision': 2}).status_code == 409
    for _ in range(2):
        assert read(client, p) == p
        client.get('/api/v1/learning/review-due')
        client.get('/api/v1/learning/review-logs')
    assert client.get('/api/v1/learning/review-logs').json()['total'] == 2


def test_edit_confirmed_card_does_not_recreate_archived_plan(client):
    login(client)
    d = deck(client)
    c = create_card(client, d['id'], status='confirmed').json()
    p = client.get('/api/v1/learning/review-plans').json()['items'][0]
    assert patch(client, p, 'archive').status_code == 200
    assert mutate(client, 'PATCH', '/learning/cards/' + c['id'], dict(revision=1, front='编辑')).status_code == 200
    assert client.get('/api/v1/learning/review-plans').json()['total'] == 1
    assert read(client, p)['status'] == 'archived'


def test_frozen_migration_backfills_only_confirmed_cards_in_active_decks(client):
    """Execute historical c4 and d58 DDL in an isolated transactional schema."""
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.shared.db import engine
    def migration(name):
        path = Path(__file__).parents[1] / 'alembic' / 'versions' / name
        spec = importlib.util.spec_from_file_location('frozen_' + name[:12], path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    with SessionLocal() as db:
        owner = db.scalar(select(User.id).where(User.username == 'admin'))
    schema = 'test_review_migration_' + uuid4().hex
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text('CREATE SCHEMA ' + schema))
            connection.execute(text('SET LOCAL search_path TO ' + schema + ',public'))
            with Operations.context(MigrationContext.configure(connection)):
                migration('c4d72e9a1f03_private_learning_cards.py').upgrade()
                decks = [uuid4(), uuid4()]
                for index, did in enumerate(decks):
                    connection.execute(text("INSERT INTO learning_decks (id,owner_id,name,status,revision,updated_at,created_at) VALUES (:id,:owner,'deck',:status,1,now(),now())"),
                                       dict(id=did, owner=owner, status='active' if index == 0 else 'archived'))
                for status, did in [('confirmed', decks[0]), ('draft', decks[0]), ('archived', decks[0]), ('confirmed', decks[1])]:
                    connection.execute(text("INSERT INTO learning_cards (id,owner_id,deck_id,kind,front,back,status,revision,updated_at,created_at) VALUES (:id,:owner,:deck,'qa','question','answer',:status,1,now(),now())"),
                                       dict(id=uuid4(), owner=owner, deck=did, status=status))
                migration('d58a3b7c901e_independent_reviews.py').upgrade()
            assert connection.scalar(text('SELECT count(*) FROM review_items')) == 1
            assert connection.scalar(text('SELECT count(*) FROM review_plans')) == 1
            row = connection.execute(text('SELECT reason,result_plan,new_snapshot FROM review_logs')).one()
            assert row.reason == 'confirmed_card_backfill'
            assert s.ReviewPlanOut.model_validate(row.result_plan).strategy == 'manual'
            assert row.new_snapshot['revision'] == 1
        finally:
            transaction.rollback()
