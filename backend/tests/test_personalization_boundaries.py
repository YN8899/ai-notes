"""Additional real-service race, cutoff and late outbound revocation coverage."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4
import pytest
from sqlalchemy import func, select
from app.modules.ai import chat_provider, chat_service
from app.modules.ai.models import AiConversation, AiRun
from app.modules.personalization.models import LearningAnalysis, LearningAnalysisPreview
from app.shared.db import SessionLocal, utcnow
from app.shared.models import AuthSession, User
from conftest import mutate
from test_social import people
from test_community import published
from test_personalization import BASE, model, save, preferences, preview, confirm


def test_concurrent_preference_first_write_returns_one_conflict(people):
    alice=people[0]['alice']
    body=preferences(alice);body.pop('updated_at')
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda section:mutate(alice,'PUT','/personalization/preferences',
            {**body,'default_section':section}), ['notes','reviews']))
    assert sorted(response.status_code for response in responses)==[200,409]
    assert preferences(alice)['revision']==1


def test_one_preview_cannot_race_into_two_paid_runs(people,model):
    alice=people[0]['alice'];save(alice,ai_analysis_enabled=True);prepared=preview(alice)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:confirm(alice,prepared)[0],range(2)))
    assert sorted(response.status_code for response in responses)==[201,409]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(LearningAnalysis))==1
        assert db.scalar(select(func.count()).select_from(AiConversation))==1
        assert db.scalar(select(func.count()).select_from(AiRun))==1
        assert db.scalar(select(func.count()).select_from(LearningAnalysisPreview))==0


@pytest.mark.parametrize('revoke',['disable','session','lease','account'])
def test_revocation_between_attempt_preparation_and_paid_post_sends_nothing(people,model,monkeypatch,revoke):
    alice=people[0]['alice'];save(alice,ai_analysis_enabled=True)
    created,_=confirm(alice);run_id=UUID(created.json()['run_id']);sent=[]
    def transport(*args,**kwargs):
        if revoke=='disable':
            assert save(alice,ai_analysis_enabled=False).status_code==200
        else:
            with SessionLocal.begin() as db:
                run=db.get(AiRun,run_id)
                if revoke=='session':db.get(AuthSession,run.auth_session_id).revoked_at=utcnow()
                elif revoke=='lease':run.lease_until=utcnow()-timedelta(seconds=1)
                else:db.get(User,run.owner_id).status='disabled'
        if kwargs['before_send']():
            sent.append(True)
            yield {'delta':'should never be sent','finish':'stop'}
    monkeypatch.setattr(chat_provider,'stream_chat',transport)
    assert chat_service.tick()
    assert sent==[]
    with SessionLocal() as db:
        run=db.get(AiRun,run_id)
        assert run.status not in {'queued','running','succeeded'}


def test_cutoff_paging_survives_new_publications_without_duplicates(people):
    alice,bob=people[0]['alice'],people[0]['bob']
    originals={published(bob,title='existing-'+str(i))['id'] for i in range(5)}
    save(alice,recommendations_enabled=True)
    first=alice.get(BASE+'/recommendations?limit=2').json()
    late=published(bob,title='after-cutoff')['id']
    received=[row['id'] for row in first['items']];cursor=first['next_cursor']
    while cursor:
        page=alice.get(BASE+'/recommendations',params={'cursor':cursor,'limit':2})
        assert page.status_code==200,page.text
        data=page.json();received.extend(row['id'] for row in data['items']);cursor=data['next_cursor']
    assert set(received)==originals and len(received)==len(set(received)) and late not in received


def test_disabling_then_reenabling_requires_new_confirmation(people,model):
    alice=people[0]['alice'];save(alice,ai_analysis_enabled=True);prepared=preview(alice)
    save(alice,ai_analysis_enabled=False);save(alice,ai_analysis_enabled=True)
    assert confirm(alice,prepared)[0].status_code==409


def test_disabled_ai_does_not_resolve_provider_or_queue_any_run(people,monkeypatch):
    from app.modules.ai import compatible
    alice=people[0]['alice']
    monkeypatch.setattr(compatible,'resolve',lambda *a,**k:(_ for _ in ()).throw(AssertionError('disabled feature resolved model')))
    day=utcnow().date().isoformat()
    assert mutate(alice,'POST','/personalization/analysis/preview',
        {'date_from':day,'date_to':day,'provider':'synthetic','model':'local-test'}).status_code==409
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AiRun))==0


def test_fact_preview_and_t32_summary_match_authoritative_statistical_scope(people,model):
    from app.modules.learning.event_models import LearningEvent
    from app.modules.learning.summary_service import aggregate
    from test_social import create_note
    alice=people[0]['alice'];owner=UUID(people[1]['alice'])
    note=create_note(alice,title='not sent to model');now=utcnow()
    with SessionLocal.begin() as db:
        db.add(LearningEvent(owner_id=owner,event_id=uuid4(),kind='reading',note_id=UUID(note['id']),
            started_at=now-timedelta(seconds=30),ended_at=now-timedelta(seconds=20),request_hash='d'*64))
    save(alice,ai_analysis_enabled=True);prepared=preview(alice)
    with SessionLocal() as db:
        from datetime import date
        day=date.fromisoformat(prepared['summary']['source']['date_from'])
        assert prepared['summary']==aggregate(db,owner,day,day)
    with SessionLocal.begin() as db:
        db.add(LearningEvent(owner_id=owner,event_id=uuid4(),kind='reading',note_id=UUID(note['id']),
            started_at=now-timedelta(seconds=50),ended_at=now-timedelta(seconds=40),request_hash='e'*64))
    # Confirmation sends exactly the already displayed server snapshot, even
    # when later recorded activity exists; it never silently broadens consent.
    created,_=confirm(alice,prepared)
    assert created.status_code==201 and created.json()['summary']==prepared['summary']
