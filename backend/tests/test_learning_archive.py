"""v3 learning content round-trip, real JPEG provenance and immutable receipts."""
from copy import deepcopy
from datetime import timedelta
from io import BytesIO
import hashlib
import shutil
from uuid import uuid4, UUID
import pytest
from PIL import Image
from sqlalchemy import delete, func, select, text
from app.modules.ai.models import AiConversation, AiRun, AiSavedNote, AiStep
from app.modules.exports import service as exports
from app.modules.exports.learning_archive import FIELDS
from app.modules.files import service as files
from app.modules.files.models import File, FileVariant, NotePage, PdfImport
from app.modules.learning import card_sources, quiz_service, quiz_schemas as q, review_service, review_schemas as r
from app.modules.learning.models import Deck, Card, CardGeneration
from app.modules.learning.quiz_models import QuizIdentity, QuizQuestion, QuizGeneration, QuizGrading, QuizAnswer
from app.modules.learning.review_models import ReviewPlan, ReviewLog
from app.modules.learning.reminder_models import LearningSettings, DailyDigest
from app.modules.learning.event_models import LearningEvent
from app.shared.db import SessionLocal, engine, utcnow
from app.shared.errors import AppError
from test_exports import account as export_account
from test_archive_restore import restore_tool, restore_database, target, _restore, _metadata
from test_site_backup import site_tool, site_support, postgres_container, site_target, _restore as restore_site


def seed(account, source_index=1):
    now = utcnow()
    stream = BytesIO()
    Image.new('RGB',(100,80),'white').save(stream,format='JPEG')
    jpeg = stream.getvalue()
    with SessionLocal.begin() as db:
        # Complete this fixture with a real bounded reading image, so restored
        # source validation must decode the same bytes rather than metadata only.
        image_run = db.get(AiRun,account.runs[source_index])
        source = deepcopy(image_run.source)
        file = db.get(File, UUID(source['pages'][0]['file_id']))
        file.width,file.height = 100,80
        reading = db.scalar(select(FileVariant).where(FileVariant.file_id==file.id,FileVariant.kind=='reading'))
        files.storage().put(reading.object_key,jpeg)
        reading.bytes = len(jpeg)
        source['pages'][0]['bytes'] = len(jpeg)
        image_run.source = deepcopy(source)
        if source['kind']=='pdf':
            source['mode']='pages'
        source['_card_identity'] = dict(sha256=file.sha256,width=100,height=80,
            reading_sha256=hashlib.sha256(jpeg).hexdigest())
        parent = AiConversation(owner_id=account.owner,title='合成学习记录',request_key=uuid4())
        deck = Deck(owner_id=account.owner,name='归档学习卡')
        db.add_all([parent,deck]);db.flush()
        runs = []
        for index in range(3):
            run = AiRun(owner_id=account.owner,conversation_id=parent.id,request_key=uuid4(),request_hash='a'*64,
                model='deepseek-flash',prompt='合成任务',answer='{}',input_messages=[],status='succeeded',
                source=deepcopy(source) if index<2 else None,finished_at=now,usage={'total_tokens':1})
            db.add(run);db.flush();runs.append(run)
        generation = CardGeneration(owner_id=account.owner,deck_id=deck.id,run_id=runs[0].id,request_key=uuid4(),
            request_hash='b'*64,kind='occlusion',count=1,source=deepcopy(source),status='succeeded',finished_at=now)
        db.add(generation);db.flush()
        card = Card(owner_id=account.owner,deck_id=deck.id,kind='occlusion',front='遮挡历史',back='答案',
            payload={'masks':[dict(x=.5,y=.1,width=.2,height=.2)]},source=deepcopy(source),status='confirmed',
            generation_id=generation.id,generation_ordinal=0)
        quiz = QuizIdentity(owner_id=account.owner,title='归档测验',status='confirmed',question_count=2)
        db.add_all([card,quiz]);db.flush()
        questions = [QuizQuestion(owner_id=account.owner,quiz_id=quiz.id,position=0,kind='short_answer',prompt='解释公式',
            correct_answer='参考',explanation='说明推理',max_points=3,source=deepcopy(source)),
            QuizQuestion(owner_id=account.owner,quiz_id=quiz.id,position=1,kind='true_false',prompt='命题成立？',
                correct_answer=True,explanation='依据',max_points=2,source=None)]
        db.add_all(questions)
        db.add(QuizGeneration(owner_id=account.owner,quiz_id=quiz.id,run_id=runs[1].id,request_key=uuid4(),
            request_hash='c'*64,title=quiz.title,kinds=['short_answer','true_false'],count=2,source=deepcopy(source),
            status='succeeded',finished_at=now))
        db.add(LearningSettings(owner_id=account.owner,timezone='America/New_York',review_time='18:30',reminder_enabled=False))
        db.add(DailyDigest(owner_id=account.owner,local_date=now.date(),timezone='America/New_York',counts=dict(note=0,card=1,quiz=1)))
        db.add(LearningEvent(owner_id=account.owner,event_id=uuid4(),kind='card_practice',card_id=card.id,
            started_at=now-timedelta(seconds=20),ended_at=now,request_hash='d'*64))
        db.flush()
        ids = dict(card=card.id,quiz=quiz.id,short=questions[0].id,boolean=questions[1].id,grading_run=runs[2].id)
    with SessionLocal() as db:
        card = db.get(Card,ids['card'])
        review_service.ensure_card_plan(db,account.owner,card);db.commit()
        plan = db.scalar(select(ReviewPlan))
        result = review_service.patch(db,account.owner,plan.id,r.ReviewPlanPatch(revision=plan.revision,
            request_key=uuid4(),operation='switch',strategy='fsrs',fsrs_mode='restart',reason='测试真实排期恢复'))
        plan = result.plan
        review_service.review(db,account.owner,plan.id,r.ReviewSubmit(revision=plan.revision,request_key=uuid4(),rating=3))
        review_service.practice(db,account.owner,r.ReviewPractice(kind='quiz',quiz_id=ids['quiz'],request_key=uuid4(),rating=2))
        attempt = quiz_service.start(db,account.owner,ids['quiz'],q.AttemptCreate(revision=1,request_key=uuid4()))
        attempt = quiz_service.save_answer(db,account.owner,attempt.id,ids['short'],q.AnswerSave(revision=attempt.revision,request_key=uuid4(),answer='学生推理'))
        attempt = quiz_service.save_answer(db,account.owner,attempt.id,ids['boolean'],q.AnswerSave(revision=attempt.revision,request_key=uuid4(),answer=True))
        attempt = quiz_service.submit(db,account.owner,attempt.id,q.Operation(revision=attempt.revision,request_key=uuid4()))
        attempt = quiz_service.review(db,account.owner,attempt.id,ids['short'],q.ManualReview(revision=attempt.revision,
            request_key=uuid4(),points=2,reason='人工复核',feedback='保留此历史'))
        answer = db.scalar(select(QuizAnswer).where(QuizAnswer.attempt_id==attempt.id,QuizAnswer.question_id==ids['short']))
        db.add(QuizGrading(owner_id=account.owner,answer_id=answer.id,answer_revision=answer.revision,
            attempt_revision=attempt.revision,run_id=ids['grading_run'],request_key=uuid4(),request_hash='e'*64,
            input=dict(question='解释公式',reference_answer='参考',rubric='说明推理',max_points=3,student_answer='学生推理'),
            status='failed',error_code='synthetic_no_call',finished_at=now))
        db.commit()
        ids['attempt'] = attempt.id
    return ids


@pytest.mark.parametrize('source_index',[1,0])
@pytest.mark.parametrize('historical',['current','removed','replaced'])
def test_v3_all_learning_collections_restore_and_read_source(export_account,restore_tool,target,tmp_path,monkeypatch,source_index,historical):
    ids = seed(export_account,source_index)
    if historical!='current':
        with SessionLocal.begin() as db:
            # Ordinary historic chat sources have their own archive policy.
            # Here only the generation/attempt learning snapshots pin this page.
            run_id = export_account.runs[source_index]
            db.execute(delete(AiSavedNote).where(AiSavedNote.run_id==run_id))
            db.execute(delete(AiStep).where(AiStep.run_id==run_id))
            db.execute(delete(AiRun).where(AiRun.id==run_id))
            frozen = db.get(Card,ids['card']).source
            page = db.get(NotePage,UUID(frozen['pages'][0]['page_id']))
            if historical=='removed':
                if source_index==0:
                    item = db.scalar(select(PdfImport).where(PdfImport.note_id==page.note_id))
                    item.status,item.completed_pages='rejected',0
                db.delete(page)
            else:
                page.file_id=export_account.files[1 if source_index==1 else 2]
                page.revision+=1
    path = tmp_path/'learning-v3.zip'
    with exports.create_archive(export_account.owner,export_account.session) as stream,path.open('wb') as out:
        shutil.copyfileobj(stream,out)
    manifest,collections = _metadata(path)
    assert manifest['version']==5
    assert all(collections[model.__tablename__] for model in FIELDS)
    assert restore_tool._load_verifier().verify_archive(path)==manifest['counts']
    _restore(restore_tool,path,target)
    monkeypatch.setattr(files.settings(),'storage_root',str(target.storage_root))
    with target.sessions() as db:
        for model in FIELDS:
            assert db.scalar(select(func.count()).select_from(model))==manifest['counts'][model.__tablename__]
        card = db.get(Card,ids['card'])
        assert card.source['_card_identity']['reading_sha256']==hashlib.sha256(jpeg_bytes(db,card)).hexdigest()
        if historical=='current':
            assert card_sources.validate(db,export_account.owner,card.source)
        else:
            with pytest.raises(AppError) as error:
                card_sources.validate(db,export_account.owner,card.source)
            assert error.value.code=='card_source_unavailable'
        attempt = quiz_service.attempt_output(db,db.get(quiz_service.QuizAttempt,ids['attempt']))
        assert attempt.total_points==4 and attempt.status=='graded'
        assert attempt.reviews[0].reason=='人工复核'
        assert attempt.reviews[0].answer_id in {a.id for a in attempt.answers}
        assert db.scalar(select(DailyDigest)).job_id is None
        assert db.scalar(select(LearningSettings)).reminder_enabled is False


def jpeg_bytes(db,card):
    return files.storage().read(card.source['pages'][0]['object_key'])


@pytest.mark.parametrize('model',[CardGeneration,QuizGeneration,QuizGrading])
def test_export_refuses_unmaterialized_learning_even_after_model_finished(export_account,model):
    seed(export_account)
    with SessionLocal.begin() as db:
        row=db.scalar(select(model))
        row.status='pending'
        assert db.get(AiRun,row.run_id).status=='succeeded'
    with pytest.raises(AppError) as error:
        exports.create_archive(export_account.owner,export_account.session)
    assert error.value.code=='export_busy'


def test_standalone_learning_field_contract_matches_explicit_server_allowlist(restore_tool):
    verifier = restore_tool._load_verifier()
    assert verifier.LEARNING_COLUMNS == {model.__tablename__:frozenset(names) for model,names in FIELDS.items()}


def test_export_rejects_changed_same_length_learning_reading_before_delivering_zip(export_account):
    ids=seed(export_account)
    with SessionLocal() as db:
        key=db.get(Card,ids['card']).source['pages'][0]['object_key']
    data=files.storage().read(key)
    # The variant's byte count still matches; only the frozen source hash can
    # detect this mutation before a user receives an unusable archive.
    files.storage().put(key,data[:-1]+bytes([data[-1]^1]))
    with pytest.raises(AppError) as error:
        exports.create_archive(export_account.owner,export_account.session)
    assert error.value.code=='export_incomplete'


def test_actual_migration_backfill_receipt_restores_without_rewriting_utc_spelling(
    export_account,restore_tool,target,tmp_path,monkeypatch,
):
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace
    path = Path(__file__).resolve().parents[1]/'alembic/versions/d58a3b7c901e_independent_reviews.py'
    spec = importlib.util.spec_from_file_location('frozen_review_backfill',path)
    migration = importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    statements=[]
    monkeypatch.setattr(migration,'op',SimpleNamespace(execute=statements.append))
    migration.upgrade()
    backfill = next(sql for sql in statements if sql.lstrip().startswith('INSERT INTO review_items'))
    with SessionLocal.begin() as db:
        deck=Deck(owner_id=export_account.owner,name='迁移之前的卡片')
        db.add(deck);db.flush()
        db.add(Card(owner_id=export_account.owner,deck_id=deck.id,kind='qa',front='旧问题',back='旧答案',status='confirmed'))
        db.flush()
        db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        db.execute(text(backfill))
        log=db.scalar(select(ReviewLog))
        original=deepcopy(log.result_plan)
        identity=log.id
        assert log.reason=='confirmed_card_backfill'
        assert all(original[key].endswith('+00:00') for key in ('next_due_at','created_at','updated_at'))
    destination=tmp_path/'actual-backfill-v3.zip'
    with exports.create_archive(export_account.owner,export_account.session) as stream,destination.open('wb') as output:
        shutil.copyfileobj(stream,output)
    _restore(restore_tool,destination,target)
    with target.sessions() as db:
        assert db.get(ReviewLog,identity).result_plan==original


def test_whole_site_retains_all_learning_and_attempt_only_asset_without_replaying_calls(
    export_account,site_tool,site_support,postgres_container,site_target,tmp_path,monkeypatch,
):
    ids=seed(export_account)
    frozen_key=f'{export_account.owner}/{uuid4()}/attempt-frozen.jpeg'
    with SessionLocal.begin() as db:
        attempt=db.get(quiz_service.QuizAttempt,ids['attempt'])
        snapshot=deepcopy(attempt.snapshot)
        page=snapshot[0]['source']['pages'][0]
        content=files.storage().read(page['object_key'])
        files.storage().put(frozen_key,content)
        page['object_key']=frozen_key
        attempt.snapshot=snapshot
        for model in (CardGeneration,QuizGeneration,QuizGrading):
            db.scalar(select(model)).status='pending'
        original_counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in FIELDS}
    bundle=tmp_path/'learning-site'
    site_tool.backup_site(engine.url.render_as_string(hide_password=False),postgres_container,bundle,
        site_support.LocalBackupStorage(files.settings().storage_root),maintenance_confirmed=True)
    manifest=site_tool.verify_site_backup(bundle)
    assert set(manifest['tables']) == set(site_tool.TABLE_NAMES)
    assert len(manifest['tables']) == len(site_tool.FROZEN_TABLE_NAMES[manifest['schema_revision']])
    frozen=next(item for item in manifest['objects'] if item['key']==frozen_key)
    assert frozen['mandatory'] and frozen['references']==[f"quiz_attempts:{ids['attempt']}:snapshot:0:pages:0"]
    result=restore_site(site_tool,bundle,site_target)
    assert result['original_fingerprints_verified'] is True
    monkeypatch.setattr(files.settings(),'storage_root',str(site_target.storage_root))
    assert files.storage().read(frozen_key)==content
    with site_target.sessions() as db:
        for model in FIELDS:
            assert db.scalar(select(func.count()).select_from(model))==original_counts[model.__tablename__]
        for model in (CardGeneration,QuizGeneration,QuizGrading):
            task=db.scalar(select(model))
            assert task.status=='failed' and task.error_code=='site_restore_interrupted'
            assert db.get(AiRun,task.run_id).usage=={'total_tokens':1}
        assert quiz_service.attempt_output(db,db.get(quiz_service.QuizAttempt,ids['attempt'])).total_points==4
