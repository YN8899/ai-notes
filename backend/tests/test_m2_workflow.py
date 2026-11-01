"""One real HTTP/service/PG workflow; only the three provider responses are mocked."""
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo
import pytest
from app.modules.ai import chat_provider
from app.modules.jobs.service import deliver_outbox
from app.modules.learning.reminder_service import dispatch
from app.shared.config import settings
from app.shared.db import utcnow
from app.worker import tick
from conftest import login,mutate
from test_files import note,upload,put,complete
from test_image_chat import image_source,mock


@pytest.fixture(autouse=True)
def isolated_provider_and_storage(monkeypatch,tmp_path):
    monkeypatch.setenv('DEEPSEEK_API_KEY','m2-synthetic-only-no-live-provider')
    settings.cache_clear()
    monkeypatch.setattr(settings(),'storage_root',str(tmp_path/'private'))
    monkeypatch.setattr(chat_provider,'create_client',lambda:(_ for _ in ()).throw(AssertionError('No real provider')))
    yield
    settings.cache_clear()


def body(response):
    assert response.status_code in {200,201,202},response.text
    return response.json()


def test_upload_ocr_search_card_quiz_independent_review_reminder_statistics_archive(client,monkeypatch,tmp_path):
    login(client)
    original=note(client)
    item,png=upload(client,original,key='m2-full-workflow-upload')
    assert put(client,item,png).status_code==200
    body(complete(client,item,original['revision']))
    assert tick()  # Real image processing produces original/reading/thumbnail.
    selected=image_source(client,original['id'])
    ocr=body(mutate(client,'POST','/ocr/documents',{**selected,'request_key':str(uuid4())}))
    ocr_calls=mock(monkeypatch,answer='M2闭环 导数是函数的瞬时变化率')
    assert tick()
    ocr=body(client.get('/api/v1/ocr/documents/'+ocr['id']))
    assert ocr['status']=='succeeded' and len(ocr_calls)==1
    body(mutate(client,'PATCH','/ocr/documents/'+ocr['id'],dict(revision=ocr['revision'],
        corrected_text='M2闭环 导数表示瞬时斜率',reviewed=True)))
    hits=body(client.get('/api/v1/search',params={'q':'导数'}))['items']
    hit=next(row for row in hits if row['kind']=='ocr' and row['note_id']==original['id'])
    resolved=body(client.get('/api/v1/search/ocr/'+hit['id']+'/source',params=dict(q='导数',
        match_hash=hit['match_hash'],source_revision=hit['source_revision'],ocr_revision=hit['ocr_revision'])))
    assert resolved['page_id']==selected['page_id'] and '瞬时斜率' in resolved['text']
    note_plan=body(mutate(client,'POST','/learning/review-plans',dict(kind='note',note_id=original['id'],request_key=str(uuid4()))))['plan']
    deck=body(mutate(client,'POST','/learning/decks',{'name':'M2闭环卡片集'}))
    source={'kind':'image',**selected}
    request=dict(deck_id=deck['id'],kind='qa',count=1,source=source)
    preview=body(mutate(client,'POST','/learning/card-generations/preview',request))
    generated=body(mutate(client,'POST','/learning/card-generations',{**request,'request_key':str(uuid4()),
        'preview_hash':preview['preview_hash'],'confirm_external':True}))
    card_calls=mock(monkeypatch,answer=json.dumps({'cards':[dict(kind='qa',front='导数表示什么？',back='瞬时斜率')]},ensure_ascii=False))
    assert tick()
    generated=body(client.get('/api/v1/learning/card-generations/'+generated['id']))
    assert generated['status']=='succeeded' and len(card_calls)==1
    card=body(client.get('/api/v1/learning/cards/'+generated['card_ids'][0]))
    assert card['status']=='draft' and body(client.get('/api/v1/learning/review-plans'))['total']==1
    card=body(mutate(client,'POST','/learning/cards/batch-review',dict(action='confirm',cards=[dict(id=card['id'],revision=card['revision'])])))[0]
    plans=body(client.get('/api/v1/learning/review-plans'))['items']
    card_plan=next(plan for plan in plans if plan['item']['card_id']==card['id'])
    assert body(client.get('/api/v1/learning/review-plans/'+note_plan['id']))==note_plan
    request=dict(title='M2闭环测验',kinds=['true_false','short_answer'],count=2,source=source)
    preview=body(mutate(client,'POST','/learning/quiz-generations/preview',request))
    gen=body(mutate(client,'POST','/learning/quiz-generations',{**request,'request_key':str(uuid4()),
        'preview_hash':preview['preview_hash'],'confirm_external':True}))
    questions=[dict(kind='true_false',prompt='导数描述瞬时变化？',correct_answer=True,max_points=1),
        dict(kind='short_answer',prompt='解释导数',correct_answer='瞬时斜率',explanation='说明变化率',max_points=2)]
    quiz_calls=mock(monkeypatch,answer=json.dumps({'questions':questions},ensure_ascii=False))
    assert tick()
    gen=body(client.get('/api/v1/learning/quiz-generations/'+gen['id']))
    assert gen['status']=='succeeded' and len(quiz_calls)==1
    quiz=body(client.get('/api/v1/learning/quizzes/'+gen['quiz_id']))
    assert quiz['status']=='draft'
    quiz=body(mutate(client,'PATCH','/learning/quizzes/'+quiz['id'],dict(revision=quiz['revision'],status='confirmed')))
    attempt=body(mutate(client,'POST','/learning/quizzes/'+quiz['id']+'/attempts',dict(revision=quiz['revision'],request_key=str(uuid4()))))
    assert all('correct_answer' not in question for question in attempt['questions'])
    for question,value in zip(attempt['questions'],[True,'导数是瞬时斜率'],strict=True):
        attempt=body(mutate(client,'PUT','/learning/quiz-attempts/'+attempt['id']+'/answers/'+question['id'],
            dict(revision=attempt['revision'],request_key=str(uuid4()),answer=value)))
    attempt=body(mutate(client,'POST','/learning/quiz-attempts/'+attempt['id']+'/submit',dict(revision=attempt['revision'],request_key=str(uuid4()))))
    assert attempt['status']=='needs_review'
    attempt=body(mutate(client,'POST','/learning/quiz-attempts/'+attempt['id']+'/answers/'+attempt['questions'][1]['id']+'/reviews',
        dict(revision=attempt['revision'],request_key=str(uuid4()),points=1,reason='M2闭环人工复核',feedback='继续说明极限')))
    assert attempt['status']=='graded' and attempt['total_points']==2 and attempt['max_points']==3
    body(mutate(client,'POST','/learning/review-plans',dict(kind='quiz',quiz_id=quiz['id'],request_key=str(uuid4()))))
    card_plan=body(mutate(client,'PATCH','/learning/review-plans/'+card_plan['id'],dict(revision=card_plan['revision'],
        request_key=str(uuid4()),operation='switch',strategy='fsrs',fsrs_mode='restart',reason='M2四档闭环')))['plan']
    for rating in (1,2,3,4):
        card_plan=body(mutate(client,'POST','/learning/review-plans/'+card_plan['id']+'/reviews',
            dict(revision=card_plan['revision'],request_key=str(uuid4()),rating=rating)))['plan']
    practice=body(mutate(client,'POST','/learning/review-items/practice',dict(kind='card',card_id=card['id'],
        plan_id=card_plan['id'],revision=card_plan['revision'],request_key=str(uuid4()),rating=3)))
    assert practice['plan']==card_plan and body(client.get('/api/v1/learning/review-plans/'+note_plan['id']))==note_plan
    prefs=body(client.get('/api/v1/learning/settings'))
    body(mutate(client,'PATCH','/learning/settings',dict(revision=prefs['revision'],timezone='Asia/Shanghai',review_time='00:00',reminder_enabled=True)))
    assert dispatch()==1 and dispatch()==0
    deliver_outbox()
    assert any(row['target']=='reviews' for row in body(client.get('/api/v1/notifications')))
    now=utcnow()
    event=dict(event_id=str(uuid4()),kind='reading',note_id=original['id'],started_at=(now-timedelta(seconds=20)).isoformat(),ended_at=now.isoformat())
    assert body(mutate(client,'POST','/learning/events',{'events':[event]}))['accepted']==1
    assert body(mutate(client,'POST','/learning/events',{'events':[event]}))['duplicates']==1
    day=now.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    statistics=body(client.get('/api/v1/learning/stats',params=dict(date_from=day,date_to=day)))
    daily=statistics['days'][0]
    assert statistics['reading_progress']==dict(recorded_notes=1,active_notes_read=1,active_notes_total=1)
    assert daily['reading_notes']==1
    assert daily['total_seconds']==20 and daily['formal_reviews']==4 and daily['practice_count']==1
    assert daily['ratings']==dict(again=1,hard=1,good=2,easy=1)
    assert daily['quiz']['graded']==1 and daily['quiz']['actual_points']==2
    calendar=body(client.get('/api/v1/learning/calendar',params=dict(date_from=day,date_to=day)))['days'][0]
    assert calendar['completed']['card']==4 and calendar['practice']['card']==1
    archive=client.post('/api/v1/exports/archive',headers={'Origin':'http://testserver','X-CSRF-Token':client.get('/api/v1/auth/csrf').json()['csrf_token']},json={})
    assert archive.status_code==200,archive.text if archive.status_code!=200 else ''
    path=tmp_path/'m2-workflow.zip';path.write_bytes(archive.content)
    spec=importlib.util.spec_from_file_location('m2_verify',Path(__file__).resolve().parents[2]/'scripts/verify_export.py')
    verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)
    counts=verifier.verify_archive(path)
    assert counts['learning_cards']==counts['quiz_attempts']==counts['learning_events']==1
