"""T24 real PostgreSQL integration: raw facts, time boundaries and bounded intake."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Barrier
from uuid import UUID, uuid4
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from app.modules.learning import stats_service as service, stats_schemas as s, review_service, quiz_service
from app.modules.learning.event_models import LearningEvent
from app.modules.learning.models import Card, Deck
from app.modules.learning.quiz_models import QuizAnswer, QuizAttempt, QuizIdentity
from app.modules.learning.reminder_models import LearningSettings
from app.modules.learning.review_models import ReviewPlan, ReviewLog
from app.modules.notes.models import Note
from app.shared.db import SessionLocal
from app.shared.models import User
from app.shared.security import hasher
from conftest import login, mutate
from test_reviews import create_note_plan, review
from test_cards import deck, create_card
from test_quizzes import confirmed, start, save, submit, short_attempt, review as manual_grade, QUESTIONS

NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(service, 'utcnow', lambda: NOW)
    monkeypatch.setattr(review_service, 'utcnow', lambda: NOW)
    monkeypatch.setattr(quiz_service, 'utcnow', lambda: NOW)


def prepare(client):
    login(client)
    note, plan, _ = create_note_plan(client)
    return note, plan


def event(note, when=None, **overrides):
    end = when or NOW-timedelta(minutes=1)
    return dict(event_id=str(uuid4()), kind='reading', note_id=note['id'],
        started_at=(end-timedelta(seconds=30)).isoformat(), ended_at=end.isoformat(), **overrides)


def post(client, events):
    return mutate(client, 'POST', '/learning/events', {'events':events})


def stats(client, day='2026-11-02', **kwargs):
    r = client.get('/api/v1/learning/stats', params=dict(date_from=day,date_to=day,**kwargs))
    assert r.status_code == 200, r.text
    return r.json()['days'][0]


def owner():
    with SessionLocal() as db:
        return db.scalar(select(User.id).where(User.username == 'admin'))


def event_count():
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(LearningEvent))


def test_atomic_batch_canonical_duplicates_conflict_and_aged_replay(client):
    note, _ = prepare(client)
    a = event(note)
    b = event(note, when=NOW-timedelta(seconds=120))
    assert post(client, [b,a,a]).json() == {'accepted':2,'duplicates':1}
    assert post(client, [a,b]).json() == {'accepted':0,'duplicates':2}
    # The same instant written with a different offset has a canonical hash.
    same = {**a, 'started_at':datetime.fromisoformat(a['started_at']).astimezone(timezone(timedelta(hours=8))).isoformat()}
    assert post(client, [same]).json()['duplicates'] == 1
    changed = {**a,'started_at':(datetime.fromisoformat(a['started_at'])+timedelta(seconds=1)).isoformat()}
    assert post(client, [event(note),changed]).status_code == 409
    assert event_count() == 2
    # Already accepted events remain replayable after the offline intake window.
    original_now = service.utcnow
    service.utcnow = lambda: NOW+timedelta(days=3)
    try:
        assert post(client,[a]).json()['duplicates'] == 1
    finally:
        service.utcnow = original_now
    assert post(client, [event(note), {**event(note), 'note_id':str(uuid4())}]).status_code == 404
    assert event_count() == 2


@pytest.mark.parametrize('change', [
    {'ended_at':NOW+timedelta(seconds=1)},
    {'started_at':NOW-timedelta(hours=24,seconds=1),'ended_at':NOW-timedelta(hours=24)},
    {'started_at':NOW-timedelta(seconds=61),'ended_at':NOW},
    {'started_at':NOW,'ended_at':NOW},
    {'started_at':NOW,'ended_at':NOW-timedelta(seconds=1)},
    {'started_at':'2026-11-02T11:58:30'},
    {'owner_id':str(uuid4())}, {'card_id':str(uuid4())}, {'kind':'quiz'}, {'kind':'other'},
])
def test_invalid_segments_are_atomic(client, change):
    note, _ = prepare(client)
    bad = {**event(note), **{k:v.isoformat() if isinstance(v,datetime) else v for k,v in change.items()}}
    assert post(client,[event(note),bad]).status_code == 422
    assert event_count() == 0


def test_batch_bounds_and_resource_visibility(client):
    note, _ = prepare(client)
    assert post(client,[]).status_code == 422
    assert post(client,[event(note)]*101).status_code == 422
    assert mutate(client,'POST','/learning/events',{'events':[event(note)],'extra':True}).status_code == 422
    d = deck(client)
    card = create_card(client,d['id']).json()
    q = confirmed(client,[QUESTIONS[0]])
    card_event = {k:v for k,v in event(note).items() if k != 'note_id'}
    card_event.update(kind='card_practice',card_id=card['id'])
    assert post(client,[card_event]).status_code == 409
    with SessionLocal.begin() as db:
        db.get(Card,UUID(card['id'])).status = 'confirmed'
    assert post(client,[card_event]).json()['accepted'] == 1
    with SessionLocal.begin() as db:
        db.get(Deck,UUID(d['id'])).status = 'archived'
    assert post(client,[{**card_event,'event_id':str(uuid4())}]).status_code == 409
    qe = {**card_event,'event_id':str(uuid4()),'kind':'quiz','quiz_id':q['id']}
    del qe['card_id']
    assert post(client,[qe]).status_code == 200
    with SessionLocal.begin() as db:
        db.get(QuizIdentity,UUID(q['id'])).question_count = 0
        db.get(Note,UUID(note['id'])).deleted_at = NOW
    assert post(client,[{**qe,'event_id':str(uuid4())}]).status_code == 409
    assert post(client,[event(note)]).status_code == 409


def test_owner_isolation_resource_and_reads(client):
    note, _ = prepare(client)
    a = event(note)
    assert post(client,[a]).status_code == 200
    with SessionLocal.begin() as db:
        db.add(User(username='other-stats',display_name='其他',role='user',password_hash=hasher.hash('other-password')))
    mutate(client,'POST','/auth/logout',{})
    login(client,'other-stats','other-password')
    assert post(client,[a]).status_code == 404
    assert stats(client)['total_seconds'] == 0
    r = client.get('/api/v1/learning/calendar',params={'date_from':'2026-11-02','date_to':'2026-11-02'})
    assert r.status_code == 200 and r.json()['days'][0]['completed']['note'] == 0


def test_two_concurrent_requests_accept_once(client):
    note, _ = prepare(client)
    uid = owner()
    data = s.ActivityBatchIn(events=[s.ActivityEventIn(**event(note))])
    barrier = Barrier(2)
    def run(_):
        with SessionLocal() as db:
            barrier.wait()
            return service.record(db,uid,data)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run,range(2)))
    assert sum(r.accepted for r in results) == sum(r.duplicates for r in results) == 1
    assert event_count() == 1


def test_overlapping_two_ten_minute_sessions_are_fifteen_minutes(client):
    note, _ = prepare(client)
    beginning = NOW-timedelta(hours=1)
    batch = []
    for offset, kind in [(0,'reading'),(5,'review')]:
        for minute in range(10):
            a = beginning + timedelta(minutes=offset+minute)
            batch.append({**event(note),'kind':kind,'started_at':a.isoformat(),
                          'ended_at':(a+timedelta(minutes=1)).isoformat()})
    assert post(client,list(reversed(batch))).json()['accepted'] == 20
    row = stats(client)
    assert row['total_seconds'] == 900
    assert row['reading_seconds'] == row['review_seconds'] == 600
    assert row['formal_reviews'] == row['distinct_review_objects'] == 0


def test_cross_local_midnight_and_current_timezone_reinterpretation(client):
    note, _ = prepare(client)
    boundary = datetime(2026,11,1,16,tzinfo=timezone.utc)
    assert post(client,[event(note,when=boundary+timedelta(seconds=15))]).status_code == 200
    response = client.get('/api/v1/learning/stats',params=dict(date_from='2026-11-01',date_to='2026-11-02'))
    assert [d['total_seconds'] for d in response.json()['days']] == [15,15]
    prefs = client.get('/api/v1/learning/settings').json()
    assert mutate(client,'PATCH','/learning/settings',dict(revision=prefs['revision'],timezone='UTC')).status_code == 200
    response = client.get('/api/v1/learning/stats',params=dict(date_from='2026-11-01',date_to='2026-11-02'))
    assert response.json()['timezone'] == 'UTC'
    assert [d['total_seconds'] for d in response.json()['days']] == [30,0]


@pytest.mark.parametrize(('day','a','b','hours'),[
    ('2026-03-08','2026-03-08T06:59:45+00:00','2026-03-08T07:00:15+00:00',23),
    ('2026-11-01','2026-11-01T05:30:00+00:00','2026-11-01T06:30:00+00:00',25),
])
def test_dst_gap_fold_uses_elapsed_utc_not_wall_clock(client,monkeypatch,day,a,b,hours):
    note, _ = prepare(client)
    end = datetime.fromisoformat(b)
    monkeypatch.setattr(service,'utcnow',lambda: end+timedelta(hours=1))
    prefs = client.get('/api/v1/learning/settings').json()
    mutate(client,'PATCH','/learning/settings',dict(revision=prefs['revision'],timezone='America/New_York'))
    # For a fold, the same local 01:30 appears twice, with separate UTC facts.
    events = [event(note,when=datetime.fromisoformat(a)),event(note,when=end)]
    assert post(client,events).status_code == 200
    row = stats(client,day=day)
    assert row['total_seconds'] == 60 and row['reading_notes'] == 1
    with SessionLocal() as db:
        _,_,_,bounds = service.window(db,owner(),date.fromisoformat(day),date.fromisoformat(day))
    assert (bounds[0][1]-bounds[0][0]).total_seconds() == hours*3600


def test_review_practice_and_quiz_recomputed_after_manual_correction(client):
    note, plan = prepare(client)
    reviewed = review(client,plan,rating=3)
    assert reviewed.status_code == 200
    assert review(client,reviewed.json()['plan'],rating=1).status_code == 200
    p = dict(kind='note',note_id=note['id'],rating=2,request_key=str(uuid4()))
    assert mutate(client,'POST','/learning/review-items/practice',p).status_code == 200
    # Review timestamps are frozen at creation; immutable history stays intact.
    attempt = short_attempt(client)
    aid = UUID(attempt['id'])
    with SessionLocal.begin() as db:
        db.get(QuizAttempt,aid).submitted_at = NOW-timedelta(minutes=2)
    row = stats(client)
    assert (row['formal_reviews'],row['distinct_review_objects'],row['practice_count']) == (2,1,1)
    assert row['ratings'] == dict(again=1,hard=1,good=1,easy=0)
    assert row['quiz'] == dict(submitted=1,graded=0,needs_review=1,actual_points=0,max_points=6,manually_corrected=0)
    corrected = manual_grade(client,attempt,points=4)
    assert corrected.status_code == 200, corrected.text
    # Deliberately stale aggregate proves stats read actual answer facts.
    with SessionLocal.begin() as db:
        db.get(QuizAttempt,aid).total_points = 0
    assert stats(client)['quiz'] == dict(submitted=1,graded=1,needs_review=0,actual_points=4,max_points=6,manually_corrected=1)
    with SessionLocal.begin() as db:
        db.get(Note,UUID(note['id'])).deleted_at = NOW
    assert stats(client)['formal_reviews'] == 2


def test_calendar_future_eligibility_history_and_filters(client):
    note, plan = prepare(client)
    assert review(client,plan,rating=1).status_code == 200
    assert mutate(client,'POST','/learning/review-items/practice',dict(kind='note',note_id=note['id'],rating=4,request_key=str(uuid4()))).status_code == 200
    d = deck(client)
    card = create_card(client,d['id'],status='confirmed').json()
    with SessionLocal.begin() as db:
        for row in db.scalars(select(ReviewPlan)):
            row.next_due_at = NOW+timedelta(days=1)
    params = dict(date_from='2026-11-02',date_to='2026-11-03')
    def get(**kw):
        r = client.get('/api/v1/learning/calendar',params={**params,**kw})
        assert r.status_code == 200, r.text
        return r.json()['days']
    days = get()
    assert days[0]['completed']['note'] == days[0]['practice']['note'] == 1
    assert days[1]['due'] == dict(note=1,card=1,quiz=0)
    assert get(kind='card')[1]['due'] == dict(note=0,card=1,quiz=0)
    assert get(kind='card')[0]['completed']['note'] == 0
    with SessionLocal.begin() as db:
        db.get(Note,UUID(note['id'])).deleted_at = NOW
        db.get(Deck,UUID(d['id'])).status = 'archived'
    days = get()
    assert days[1]['due'] == dict(note=0,card=0,quiz=0)
    assert days[0]['completed']['note'] == 1


def test_date_ranges_scan_limit_daily_cap_and_no_silent_truncation(client,monkeypatch):
    note, _ = prepare(client)
    for endpoint in ('stats','calendar'):
        for params in [dict(date_from='2026-11-02',date_to='2026-11-01'),
                       dict(date_from='2026-01-01',date_to='2026-04-04'),
                       dict(date_from='bad'),dict(date_to='9999-12-31')]:
            assert client.get('/api/v1/learning/'+endpoint,params=params).status_code == 422
    assert client.get('/api/v1/learning/calendar',params={'kind':'reading'}).status_code == 422
    monkeypatch.setattr(service,'DAILY_EVENT_CAP',1)
    assert post(client,[event(note),event(note)]).status_code == 429
    assert event_count() == 0
    a = event(note)
    assert post(client,[a,a]).json() == {'accepted':1,'duplicates':1}
    monkeypatch.setattr(service,'SCAN_CAP',0)
    r = client.get('/api/v1/learning/stats',params={'date_from':'2026-11-02','date_to':'2026-11-02'})
    assert r.status_code == 422 and r.json()['code'] == 'statistics_scan_limit'


def test_database_constraints_are_owner_scoped_and_bound_duration(client):
    note, _ = prepare(client)
    uid = owner()
    valid = dict(owner_id=uid,request_hash='f'*64,**s.ActivityEventIn(**event(note)).model_dump())
    with SessionLocal.begin() as db:
        other = User(username='raw-other',display_name='other',role='user',password_hash=hasher.hash('raw-password'))
        db.add(other)
        db.flush()
        other_id = other.id
    for changes in [dict(owner_id=other_id),dict(kind='quiz'),dict(ended_at=NOW),dict(card_id=uuid4())]:
        with pytest.raises(IntegrityError), SessionLocal.begin() as db:
            db.add(LearningEvent(**{**valid,**changes}))
            db.flush()


def test_exact_offline_duration_range_boundaries_and_quiz_calendar(client):
    note, _ = prepare(client)
    a = {**event(note), 'started_at':(NOW-timedelta(hours=24)).isoformat(),
         'ended_at':(NOW-timedelta(hours=24)+timedelta(seconds=60)).isoformat()}
    assert post(client,[a]).status_code == 200
    for endpoint in ('stats','calendar'):
        r = client.get('/api/v1/learning/'+endpoint,params=dict(date_from='2026-08-02',date_to='2026-11-02'))
        assert r.status_code == 200 and len(r.json()['days']) == 93
    q = confirmed(client,[QUESTIONS[0]])
    attempt = start(client,q)
    answered = save(client,attempt,0,0)
    assert answered.status_code == 200
    submit(client,answered.json())
    params = dict(date_from='2026-11-02',date_to='2026-11-02')
    row = client.get('/api/v1/learning/calendar',params={**params,'kind':'quiz'}).json()['days'][0]
    assert row['quiz'] == dict(submitted=1,graded=1,needs_review=0,actual_points=2,max_points=2,manually_corrected=0)
    row = client.get('/api/v1/learning/calendar',params={**params,'kind':'note'}).json()['days'][0]
    assert row['quiz']['submitted'] == 0


@pytest.mark.parametrize('source',['review','due','answers'])
def test_scan_caps_apply_to_each_raw_fact_source(client,monkeypatch,source):
    _, plan = prepare(client)
    if source == 'review':
        assert review(client,plan).status_code == 200
        monkeypatch.setattr(service,'SCAN_CAP',0)
        endpoint = 'stats'
    elif source == 'due':
        with SessionLocal.begin() as db:
            db.get(ReviewPlan,UUID(plan['id'])).next_due_at = NOW+timedelta(minutes=1)
        monkeypatch.setattr(service,'SCAN_CAP',0)
        endpoint = 'calendar'
    else:
        q = confirmed(client,[QUESTIONS[0],QUESTIONS[2]])
        submit(client,start(client,q))
        # A single attempt is below the cap; its two answer rows exceed it.
        monkeypatch.setattr(service,'SCAN_CAP',1)
        endpoint = 'stats'
    r = client.get('/api/v1/learning/'+endpoint,params=dict(date_from='2026-11-02',date_to='2026-11-02'))
    assert r.status_code == 422 and r.json()['code'] == 'statistics_scan_limit'


@pytest.mark.parametrize('endpoint',['stats','calendar'])
def test_endpoint_year_bounds_precede_default_date_arithmetic(client,endpoint):
    login(client)
    path = '/api/v1/learning/'+endpoint
    for params in [dict(date_to='0001-01-01'), dict(date_to='0001-01-29'),
                   dict(date_from='0001-01-01',date_to='0001-01-02'),
                   dict(date_to='1899-12-31'), dict(date_to='1900-01-01'),
                   dict(date_to='1900-01-29'), dict(date_from='9999-01-01')]:
        response = client.get(path,params=params)
        assert response.status_code == 422, response.text
        assert response.json()['code'] == 'statistics_range_invalid'
    for params, first, last, count in [
        (dict(date_from='1900-01-01',date_to='1900-01-01'),'1900-01-01','1900-01-01',1),
        (dict(date_to='1900-01-30'),'1900-01-01','1900-01-30',30),
        ({},'2026-10-04','2026-11-02',30),
    ]:
        response = client.get(path,params=params)
        assert response.status_code == 200, response.text
        result = response.json()
        assert (result['date_from'],result['date_to'],len(result['days'])) == (first,last,count)


@pytest.mark.parametrize(('field','value'),[
    ('started_at','0001-01-01T00:00:00+14:00'),
    ('ended_at','9999-12-31T23:59:00-14:00'),
])
def test_activity_utc_normalization_overflow_is_validation_error(client,field,value):
    note, _ = prepare(client)
    normal = event(note)
    response = post(client,[normal,{**event(note),field:value}])
    assert response.status_code == 422, response.text
    assert response.json()['code'] == 'validation_error'
    assert event_count() == 0
    response = post(client,[normal])
    assert response.status_code == 200, response.text
    assert response.json() == dict(accepted=1,duplicates=0)


def test_reading_progress_distinct_period_current_coverage_and_owner_isolation(client):
    note, _ = prepare(client)
    uid = owner()
    with SessionLocal.begin() as db:
        second = Note(owner_id=uid,title='second')
        third = Note(owner_id=uid,title='review only')
        other = User(username='reading-other',display_name='其他',role='user',password_hash=hasher.hash('other-password'))
        db.add_all([second,third,other])
        db.flush()
        other_note = Note(owner_id=other.id,title='foreign')
        db.add(other_note)
        db.flush()
        second_id, third_id = str(second.id), str(third.id)
        foreign_owner, foreign_note = other.id, str(other_note.id)
    boundary = datetime(2026,11,1,16,tzinfo=timezone.utc)
    crossing = event(note,when=boundary+timedelta(seconds=15))
    same_note_other_device = event(note)
    batch = [crossing,same_note_other_device,{**same_note_other_device,'event_id':str(uuid4())},
             event({'id':second_id}),{**event({'id':third_id}),'kind':'review'}]
    assert post(client,batch+[crossing]).json() == dict(accepted=5,duplicates=1)
    with SessionLocal() as db:
        service.record(db,foreign_owner,s.ActivityBatchIn(events=[s.ActivityEventIn(**event({'id':foreign_note}))]))
    params = dict(date_from='2026-11-01',date_to='2026-11-02')
    def get():
        response = client.get('/api/v1/learning/stats',params=params)
        assert response.status_code == 200, response.text
        return response.json()
    result = get()
    assert [row['reading_notes'] for row in result['days']] == [1,2]
    assert result['reading_progress'] == dict(recorded_notes=2,active_notes_read=2,active_notes_total=3)
    with SessionLocal.begin() as db:
        db.get(Note,UUID(note['id'])).deleted_at = NOW
    result = get()
    assert [row['reading_notes'] for row in result['days']] == [1,2]
    assert result['reading_progress'] == dict(recorded_notes=2,active_notes_read=1,active_notes_total=2)
    with SessionLocal.begin() as db:
        db.get(Note,UUID(note['id'])).deleted_at = None
    assert get()['reading_progress'] == dict(recorded_notes=2,active_notes_read=2,active_notes_total=3)
    mutate(client,'POST','/auth/logout',{})
    login(client,'reading-other','other-password')
    assert get()['reading_progress'] == dict(recorded_notes=1,active_notes_read=1,active_notes_total=1)


def test_reading_progress_empty_and_review_only_keep_real_denominator(client):
    note, _ = prepare(client)
    with SessionLocal.begin() as db:
        db.add_all([Note(owner_id=owner(),title='unread'),Note(owner_id=owner(),title='recycled',deleted_at=NOW)])
    params = dict(date_from='2026-11-02',date_to='2026-11-02')
    for events in ([],[{**event(note),'kind':'review'}]):
        if events:
            assert post(client,events).status_code == 200
        response = client.get('/api/v1/learning/stats',params=params)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['days'][0]['reading_notes'] == 0
        assert result['reading_progress'] == dict(recorded_notes=0,active_notes_read=0,active_notes_total=2)


def test_reading_progress_clips_window_and_excludes_touching_edges(client,monkeypatch):
    note, _ = prepare(client)
    uid = owner()
    with SessionLocal.begin() as db:
        before, after = Note(owner_id=uid,title='before'),Note(owner_id=uid,title='after')
        db.add_all([before,after])
        db.flush()
        before_id, after_id = str(before.id), str(after.id)
    start = datetime(2026,11,1,16,tzinfo=timezone.utc)
    end = start+timedelta(days=1)
    monkeypatch.setattr(service,'utcnow',lambda: start+timedelta(hours=1))
    assert post(client,[event(note,when=start+timedelta(seconds=15)),
                        event({'id':before_id},when=start)]).status_code == 200
    monkeypatch.setattr(service,'utcnow',lambda: end+timedelta(hours=1))
    assert post(client,[event({'id':after_id},when=end+timedelta(seconds=30))]).status_code == 200
    response = client.get('/api/v1/learning/stats',params=dict(date_from='2026-11-02',date_to='2026-11-02'))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['days'][0]['reading_notes'] == 1 and result['days'][0]['reading_seconds'] == 15
    assert result['reading_progress'] == dict(recorded_notes=1,active_notes_read=1,active_notes_total=3)
