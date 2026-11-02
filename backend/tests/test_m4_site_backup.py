"""Actual final-M4 mixed-data pg_dump/restore, with no external service calls."""
from copy import deepcopy
from datetime import timedelta
import base64
import hashlib
import io
import json
from types import SimpleNamespace
from pathlib import Path
from uuid import uuid4

from PIL import Image
from pydantic import TypeAdapter
import pytest
from sqlalchemy import func, select

from app.modules.ai import chat_service, pdf_context, tools
from app.modules.ai.models import AiConversation, AiRun, AiStep, AiToolCall
from app.modules.exports import service as exports
from app.modules.exports.social_models import SocialArchiveRecord, SocialArchiveAsset
from app.modules.files.images import process_image
from app.modules.files.models import File, FileVariant, NotePage, PdfImport, UploadSession
from app.modules.files.storage import LocalStorage
from app.modules.learning.summary_service import aggregate
from app.modules.notes.models import Note
from app.modules.notes import service as notes
from app.modules.notifications.feed_models import NotificationCounter
from app.modules.notifications import feed
from app.modules.notifications.models import PushSubscription
from app.modules.offline import service as offline, schemas as offline_schemas
from app.modules.offline.models import OfflineSyncReceipt, OfflineSyncState
from app.modules.personalization import contract, schemas as home_schemas
from app.modules.personalization.models import UserHomePreferences, LearningAnalysisPreview, LearningAnalysis
from app.shared.config import settings
from app.shared.db import SessionLocal, engine, utcnow
from app.shared.errors import AppError
from app.shared.models import AuthSession, Job, Outbox, User
from test_m3_site_backup import m3_backup_account
from test_site_backup import site_tool, site_support, postgres_container, site_target, _backup, _restore, _rows


M4_TABLES = {"push_subscriptions", "offline_sync_states", "offline_sync_receipts", "ai_tool_calls",
             "user_home_preferences", "learning_analysis_previews", "learning_analyses", "notification_counters"}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def _pdf(db, a):
    """Actual PDF original, two physical JPEG pages, one retired identity."""
    pages = [Image.new("RGB", (24, 30), color) for color in ("red", "blue")]
    output = io.BytesIO()
    pages[0].save(output, format="PDF", save_all=True, append_images=pages[1:])
    pdf = output.getvalue()
    note = Note(owner_id=a.third, kind="image", title="M4原PDF与移除页历史")
    db.add(note); db.flush()

    def file(content, mime, name, **extra):
        identity = uuid4()
        key = f"m4/{a.third}/{identity}/original"
        a.store.put(key, content); a.contents[key] = content
        row = File(id=identity, owner_id=a.third, object_key=key, filename=name, mime=mime,
                   expected_bytes=len(content), bytes=len(content), sha256=digest(content), status="ready", **extra)
        db.add(row); db.flush()
        return row

    source = file(pdf, "application/pdf", "合成原PDF.pdf")
    db.add(UploadSession(owner_id=a.third, file_id=source.id, note_id=note.id, request_key="m4-pdf-original",
                         status="completed", expires_at=utcnow()+timedelta(hours=1)))
    item = PdfImport(owner_id=a.third, note_id=note.id, source_file_id=source.id, request_hash="f"*64,
                     status="ready", page_count=2, completed_pages=2)
    db.add(item)
    identities, reading = [], []
    for index, image in enumerate(pages):
        output = io.BytesIO(); image.save(output, format="JPEG")
        raw = output.getvalue(); processed = process_image(raw, "image/jpeg", 20_000_000)
        original = file(raw, "image/jpeg", f"合成第{index+1}页.jpg", width=24, height=30)
        page = NotePage(owner_id=a.third, note_id=note.id, file_id=original.id, position=index, pdf_page_number=index+1)
        db.add(page); db.flush(); identities.append(page)
        for kind, value in (("reading", processed.reading), ("thumbnail", processed.thumbnail)):
            key=f"m4/{a.third}/{original.id}/{kind}.jpg"
            a.store.put(key,value); a.contents[key]=value
            db.add(FileVariant(file_id=original.id, kind=kind, object_key=key, bytes=len(value)))
            if kind=="reading": reading.append((key,len(value)))
    db.flush(); notes.save_snapshot(db,note)
    identities[0].deleted_at=utcnow(); db.flush(); identities[1].position=0
    note.revision=2; db.flush(); notes.save_snapshot(db,note)
    frozen={"kind":"pdf","note_id":str(note.id),"title":note.title,"revision":1,"mode":"pages","pages":[{
        "page_id":str(identities[0].id),"number":1,"file_id":str(identities[0].file_id),
        "object_key":reading[0][0],"bytes":reading[0][1]}]}
    return SimpleNamespace(note=note.id,pages=[p.id for p in identities],original=source.id,item=item.id,source=frozen)


@pytest.fixture
def m4_mixed_account(m3_backup_account,monkeypatch):
    a=m3_backup_account
    monkeypatch.setattr(settings(),"storage_root",str(a.store.root))
    with SessionLocal.begin() as db:
        owner=db.get(User,a.owner); peer=db.get(User,a.outsider)
        state=offline.ensure_state(db,owner)
        operation=TypeAdapter(offline_schemas.SyncIn).validate_python(dict(sync_epoch=str(state.epoch),operation_id=str(uuid4()),
            kind="note.create",resource_id=str(uuid4()),base_revision=None,payload={"title":"M4已同步的独立笔记","kind":"text"}))
        receipt=offline.apply(db,owner,operation).model_dump(mode="json")
        update=TypeAdapter(offline_schemas.SyncIn).validate_python(dict(sync_epoch=str(state.epoch),operation_id=str(uuid4()),
            kind="note.update",resource_id=receipt["resource_id"],base_revision=1,payload={"title":"M4已同步后继续编辑"}))
        offline.apply(db,owner,update)
        offline.ensure_state(db,peer)
        pdf=_pdf(db,a)
        # Make prior inert M3 history strict-account-valid too, without changing
        # the shared M3 fixture or granting an external participant anything.
        archived=db.get(SocialArchiveRecord,a.archive); attachment=db.get(SocialArchiveAsset,a.offline_asset)
        historical_thread=uuid4()
        basic={"id":str(a.third),"username":"m3-third","display_name":"m3-third"}
        db.add(SocialArchiveRecord(owner_id=a.third,kind="chat_thread",source_id=historical_thread,payload={"offline_only":True,
            "peer":{"id":str(a.outsider),"username":"m3-peer","display_name":"m3-peer"},"last_seq":1,"last_read_seq":1,"peer_read_seq":0}))
        archived.payload={"offline_only":True,"thread_id":str(historical_thread),"seq":1,"sender":basic,"sender_is_self":True,
            "body":"M4保留的离线历史正文","client_message_id":"m4-history-1","attachment_ids":[str(attachment.source_id)]}
        selected={"fingerprint":"a"*64,"capabilities":{"text":True,"vision":False,"tools":True,"streaming":True}}
        conversation=AiConversation(owner_id=a.owner,title="M4工具历史",request_key=uuid4())
        pdf_conversation=AiConversation(owner_id=a.third,title="M4移除PDF页来源",request_key=uuid4())
        db.add_all([conversation,pdf_conversation]); db.flush()
        session=db.get(AuthSession,a.session)
        success_ids=[]; active_ids=[]; pending_tools=[]
        today=utcnow().date()
        selection=tools.ToolSelection.model_validate({"tools":["learning_summary"],"tool_scope":{"learning_summary":{
            "date_from":today.isoformat(),"date_to":today.isoformat()}}})
        scope=tools.freeze_scope(db,a.owner,selection)
        result,sources=tools.invoke(db,a.owner,scope,"learning_summary",tools.SummaryArgs())
        for index,status in enumerate(("succeeded","running")):
            run=AiRun(owner_id=a.owner,conversation_id=conversation.id,request_key=uuid4(),request_hash="a"*64,
                provider="synthetic",model="m4-no-network",provider_snapshot=selected,tools=["learning_summary"],tool_scope=scope,
                auth_session_id=session.id,prompt="只解释本人合成学习汇总",input_messages=[{"role":"user","content":"合成授权输入"}],
                answer="已保留的工具回答" if index==0 else "未完成的部分回答",status=status,usage={"total_tokens":7},
                lease_until=utcnow()+timedelta(minutes=1) if index else None,finished_at=utcnow() if index==0 else None)
            db.add(run); db.flush()
            step=AiStep(run_id=run.id,position=0,kind="chat",pages=[],status="succeeded" if index==0 else "running",
                answer=run.answer,usage={"total_tokens":7})
            db.add(step); db.flush()
            call=AiToolCall(owner_id=a.owner,run_id=run.id,step_id=step.id,position=0,provider_call_id=f"m4-call-{index}",
                tool_name="learning_summary",arguments={},result=result if index==0 else None,sources=sources if index==0 else [],
                status="succeeded" if index==0 else "pending",finished_at=utcnow() if index==0 else None)
            db.add(call); db.flush()
            (success_ids if index==0 else active_ids).append(run.id)
            if index: pending_tools.append(call.id)
        pdf_run=AiRun(owner_id=a.third,conversation_id=pdf_conversation.id,request_key=uuid4(),request_hash="b"*64,
            model="deepseek-flash",prompt="历史PDF选页",input_messages=[],answer="保留原页号1引用",status="succeeded",source=pdf.source,finished_at=utcnow())
        db.add(pdf_run)
        summary=aggregate(db,a.owner,today,today)
        preferences=home_schemas.Preferences(ai_analysis_enabled=True,default_section="reviews",subjects=["数学"])
        db.add(UserHomePreferences(owner_id=a.owner,revision=3,preferences=preferences.model_dump()))
        db.add(UserHomePreferences(owner_id=a.outsider,revision=2,preferences=home_schemas.Preferences().model_dump()))
        payload=contract.preview_payload(summary,"synthetic","m4-no-network",selected)
        for status in ("succeeded","queued"):
            reserved=AiConversation(owner_id=a.owner,title="私人学习事实解释",request_key=uuid4())
            db.add(reserved); db.flush()
            run=AiRun(owner_id=a.owner,conversation_id=reserved.id,request_key=uuid4(),request_hash="c"*64,
                provider="synthetic",model="m4-no-network",provider_snapshot=selected,tools=[],tool_scope=None,
                auth_session_id=session.id,prompt="解释本次明确确认的本人学习汇总",input_messages=payload["input_messages"],
                answer="数字事实，不推断掌握" if status=="succeeded" else "",status=status,
                usage={"total_tokens":5} if status=="succeeded" else None,finished_at=utcnow() if status=="succeeded" else None)
            db.add(run); db.flush()
            db.add(LearningAnalysis(owner_id=a.owner,run_id=run.id,request_key=run.request_key,request_hash="c"*64,
                preview_hash=contract.digest(payload),snapshot=payload))
            (success_ids if status=="succeeded" else active_ids).append(run.id)
        db.add(LearningAnalysisPreview(owner_id=a.owner,auth_session_id=session.id,preferences_revision=3,summary=summary,
            input_messages=payload["input_messages"],provider="synthetic",model="m4-no-network",provider_snapshot=selected,
            preview_hash=contract.digest(payload),input_bytes=sum(len(x["content"].encode()) for x in payload["input_messages"]),
            expires_at=utcnow()+timedelta(minutes=10)))
        # Explicitly inert synthetic endpoint/keys; no sending service is run.
        db.add(PushSubscription(owner_id=a.owner,session_id=session.id,endpoint_hash="d"*64,
            endpoint="https://fcm.googleapis.com/m4-synthetic-never-contacted",p256dh="B"*87,auth="A"*22,vapid_key_hash="e"*64,enabled=True))
        notice_ids=[]
        for user_id,count in ((a.owner,275),(a.outsider,3),(a.third,2)):
            current=[]
            for index in range(count):
                job=Job(owner_id=user_id,kind="diagnostic",status="succeeded",dedupe_key=f"m4-notice-{index}",payload={})
                db.add(job); db.flush()
                event=Outbox(user_id=user_id,job_id=job.id,kind="job.succeeded",payload={"message":f"M4通知{index}"},delivered_at=utcnow())
                db.add(event); db.flush()
                current.append(feed.persist(db,event,now=utcnow()-timedelta(minutes=index%7)).id)
            feed.read(db,user_id,current[0]); feed.read(db,user_id,current[-1])
            notice_ids.extend(current)
        db.flush()
    return SimpleNamespace(**{**vars(a),"pdf":pdf,"operation":operation,"receipt":receipt,"active_runs":active_ids,
        "succeeded_runs":success_ids,"pending_tools":pending_tools,"notice_ids":notice_ids,"original_rows":_rows(engine)})


def test_real_final_m4_mixed_roundtrip_preserves_facts_and_revokes_runtime(
    site_tool,site_support,postgres_container,site_target,m4_mixed_account,tmp_path,monkeypatch,
):
    a=m4_mixed_account; before=a.original_rows
    assert M4_TABLES <= set(before) and all(before[name] for name in M4_TABLES)
    path=tmp_path/"m4-final-site"
    _backup(site_tool,site_support,a,postgres_container,path)
    manifest=site_tool.verify_site_backup(path)
    assert manifest["schema_revision"]=="29de03d04789" and len(manifest["tables"])==74
    restored=_restore(site_tool,path,site_target)
    assert restored["original_fingerprints_verified"] is True
    after=_rows(site_target.engine)
    normalized={"sessions","jobs","realtime_tickets","community_snapshots","push_subscriptions","learning_analysis_previews",
                "user_home_preferences","ai_runs","ai_steps","ai_tool_calls"}
    for table in before.keys()-normalized:
        assert after[table]==before[table],table
    assert after["push_subscriptions"]==after["realtime_tickets"]==after["learning_analysis_previews"]==[]
    assert len(after["users"])==3
    for row in after["sessions"]:
        old=next(x for x in before["sessions"] if x["id"]==row["id"])
        assert row["revoked_at"] is not None
        assert row=={**old,"revoked_at":row["revoked_at"]}
    for row in after["jobs"]:
        old=next(x for x in before["jobs"] if x["id"]==row["id"])
        expected=old if old["status"] not in {"queued","running"} else {**old,"status":"failed",
            "error_code":"site_restore_interrupted","lease_until":None,"worker_token":None}
        assert row==expected
    for row in after["community_snapshots"]:
        old=next(x for x in before["community_snapshots"] if x["id"]==row["id"])
        assert row==({**old,"status":"failed","error_code":"site_restore_interrupted"} if old["status"]=="preparing" else old)
    for row in after["user_home_preferences"]:
        old=next(x for x in before["user_home_preferences"] if x["owner_id"]==row["owner_id"])
        assert row=={**old,"preferences":{**old["preferences"],"ai_analysis_enabled":False}}
    for row in after["ai_runs"]:
        old=next(x for x in before["ai_runs"] if x["id"]==row["id"])
        expected={**old,"auth_session_id":None}
        if old["status"] in {"queued","running"}:
            assert row["finished_at"] is not None
            expected.update(status="interrupted",error_code="site_restore_interrupted",lease_until=None,finished_at=row["finished_at"])
        assert row==expected
    for row in after["ai_steps"]:
        old=next(x for x in before["ai_steps"] if x["id"]==row["id"])
        assert row==({**old,"status":"failed","error_code":"site_restore_interrupted"} if old["status"]=="running" else old)
    for row in after["ai_tool_calls"]:
        old=next(x for x in before["ai_tool_calls"] if x["id"]==row["id"])
        expected=old if old["status"]!="pending" else {**old,"status":"interrupted","error_code":"site_restore_interrupted","finished_at":row["finished_at"]}
        assert row==expected
        if old["status"]=="pending": assert row["finished_at"] is not None
    actual={p.relative_to(site_target.storage_root).as_posix():p.read_bytes() for p in site_target.storage_root.rglob('*') if p.is_file()}
    assert actual==a.contents
    with site_target.sessions() as db:
        assert db.get(NotePage,a.pdf.pages[0]).deleted_at is not None
        assert [db.get(NotePage,p).pdf_page_number for p in a.pdf.pages]==[1,2]
        item=db.get(PdfImport,a.pdf.item)
        assert (item.page_count,item.completed_pages,item.source_file_id)==(2,2,a.pdf.original)
        pdf_context.validate(db,a.third,a.pdf.source,historical=True)
        with pytest.raises(AppError): pdf_context.validate(db,a.third,a.pdf.source)
        assert offline.apply(db,db.get(User,a.owner),a.operation).model_dump(mode="json")==a.receipt
        assert db.get(Note,a.operation.resource_id).revision==2
        first=feed.page(db,a.owner,0,100); second=feed.page(db,a.owner,first['next_after_seq'],100)
        third=feed.page(db,a.owner,second['next_after_seq'],100)
        assert [len(p['items']) for p in (first,second,third)]==[100,100,75]
        assert [n.seq for page in (first,second,third) for n in page['items']]==list(range(1,276))
        assert third['head_seq']==275 and third['read_revision']==2 and not third['has_more']
    assert _rows(site_target.engine)==after  # replay was a read of the durable receipt
    monkeypatch.setattr(chat_service,"SessionLocal",site_target.sessions)
    assert chat_service.claim() is None
    assert _rows(engine)==before
    # Actual per-owner portable archives must pass the standalone checker too;
    # source data/credentials are never used to authenticate the restored site.
    monkeypatch.setattr(exports,"SessionLocal",site_target.sessions)
    monkeypatch.setattr(settings(),"storage_root",str(site_target.storage_root))
    import verify_export
    portable=[]
    for owner_id in (a.owner,a.outsider,a.third):
        with site_target.sessions.begin() as db:
            session=AuthSession(user_id=owner_id,token_hash=uuid4().hex,csrf_hash=uuid4().hex,expires_at=utcnow()+timedelta(hours=1))
            db.add(session); db.flush(); identity=session.id
        archive=tmp_path/f"restored-{owner_id}.zip"
        with exports.create_archive(owner_id,identity) as stream,archive.open('wb') as output:
            output.write(stream.read())
        counts=verify_export.verify_archive(archive)
        assert len(counts)==37 and not ({"push_subscriptions","offline_sync_states","offline_sync_receipts",
            "notification_counters","learning_analysis_previews"}&set(counts))
        portable.append({"owner_id":str(owner_id),"counts":counts,"sha256":digest(archive.read_bytes())})
    source_after=_rows(engine)
    assert source_after==before
    evidence={"result":"PASS","revision":manifest['schema_revision'],"tables":len(manifest['tables']),
        "source_sha256_before":digest(site_tool._json(before)),"source_sha256_after":digest(site_tool._json(source_after)),
        "all_m4_tables_nonempty":sorted(M4_TABLES),"owners":3,"objects":len(actual),
        "objects_sha256":{key:digest(value) for key,value in actual.items()},
        "historical_pdf_tombstone_preserved":True,"offline_epoch_receipts_preserved_and_replay_no_write":True,
        "notification_pages":[100,100,75],"notification_head":275,"notification_read_revision":2,
        "unknown_paid_runs_terminal_and_unclaimable":True,"temporary_previews_and_device_capabilities_cleared":True,
        "portable_v5_checks":portable,"original_fingerprints_verified":restored['original_fingerprints_verified']}
    (tmp_path/'mixed-site-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
