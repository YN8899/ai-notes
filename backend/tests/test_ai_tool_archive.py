"""Actual v5 nonempty custom-provider/tool history roundtrip and malicious archives."""
from copy import deepcopy
from datetime import date
import hashlib
import json
import shutil
from uuid import uuid4
import zipfile

import pytest
from sqlalchemy import select

from app.modules.ai.models import AiConversation, AiRun, AiStep, AiToolCall
from app.modules.ai.text_context import canonical_text
from app.modules.exports import service as exports
from app.modules.learning.summary_service import aggregate
from app.modules.notes.models import Note
from app.shared.db import SessionLocal, utcnow
from test_exports import account
from test_archive_restore import restore_tool, restore_database, target, _restore


@pytest.fixture
def tool_archive(account, tmp_path):
    with SessionLocal.begin() as db:
        note = db.get(Note, account.text)
        text = canonical_text(note.body_json)
        summary = aggregate(db, account.owner, date(2026,11,1), date(2026,11,1))
        scope = {"version":1, "notes":[{"note_id":str(note.id),"revision":note.revision,"title":note.title}],"learning_summary":summary["source"]}
        parent = AiConversation(owner_id=account.owner, request_key=uuid4(), title="Tool history")
        db.add(parent);db.flush()
        run = AiRun(owner_id=account.owner, conversation_id=parent.id, request_key=uuid4(), request_hash="a"*64,
            model="retired/"+"m"*180, provider="retired_custom", provider_snapshot={"fingerprint":"b"*64,"capabilities":{"text":True,"vision":False,"tools":True,"streaming":True}},
            tools=["read_note_excerpt","learning_summary"], tool_scope=scope, prompt="Question", input_messages=[],
            answer="Recorded answer", status="succeeded", finished_at=utcnow(), auth_session_id=account.session)
        db.add(run);db.flush()
        step = AiStep(run_id=run.id, position=0, kind="chat", pages=[], status="succeeded", answer="", usage=None)
        final = AiStep(run_id=run.id, position=1, kind="chat", pages=[], status="succeeded", answer=run.answer, usage={"total_tokens":5,"prompt_tokens":3,"completion_tokens":2})
        db.add_all([step,final]);db.flush()
        source={"note_id":str(note.id),"revision":note.revision,"title":note.title,"start":0,"end":len(text)}
        db.add(AiToolCall(owner_id=account.owner,run_id=run.id,step_id=step.id,position=0,provider_call_id="read_1",tool_name="read_note_excerpt",
            arguments={"note_id":str(note.id),"start":0,"length":1000},result=json.dumps({**source,"excerpt":text},ensure_ascii=False),sources=[source],status="succeeded",finished_at=utcnow()))
        db.add(AiToolCall(owner_id=account.owner,run_id=run.id,step_id=step.id,position=1,provider_call_id="summary_1",tool_name="learning_summary",
            arguments={},result=json.dumps(summary,ensure_ascii=False),sources=[summary["source"]],status="succeeded",finished_at=utcnow()))
        identity=run.id
    path=tmp_path/'tool-history-v5.zip'
    with exports.create_archive(account.owner,account.session) as stream,path.open('wb') as output:shutil.copyfileobj(stream,output)
    return path,identity,account


def rewrite(path, target, mutate):
    with zipfile.ZipFile(path) as archive: payloads={name:archive.read(name) for name in archive.namelist()}
    manifest=json.loads(payloads['manifest.json']); data=json.loads(payloads['data.json'])
    mutate(manifest,data['collections'])
    raw=json.dumps(data,ensure_ascii=False,allow_nan=False).encode();payloads['data.json']=raw
    manifest['counts']={name:len(rows) for name,rows in data['collections'].items()}
    for entry in manifest['payloads']:
        if entry['path']=='data.json':entry.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    payloads['manifest.json']=json.dumps(manifest).encode()
    with zipfile.ZipFile(target,'w') as archive:
        for name,raw in payloads.items():archive.writestr(name,raw)
    return target


def test_v5_nonempty_custom_tools_and_summary_verify_real_empty_database_restore(tool_archive,restore_tool,target):
    path,identity,account=tool_archive
    verifier=restore_tool._load_verifier();counts=verifier.verify_archive(path)
    assert counts['ai_tool_calls']==2
    with zipfile.ZipFile(path) as archive:
        raw=archive.read('data.json').decode();assert 'auth_session_id' not in raw and 'api_key' not in raw and 'base_url' not in raw
    _restore(restore_tool,path,target)
    with target.sessions() as db:
        run=db.get(AiRun,identity)
        assert run.provider=='retired_custom' and len(run.model)>64 and run.usage is None
        assert run.auth_session_id is None and run.input_messages==[] and run.status=='succeeded'
        calls=list(db.scalars(select(AiToolCall).where(AiToolCall.run_id==identity).order_by(AiToolCall.position)))
        assert len(calls)==2 and calls[0].sources[0]['note_id']==str(account.text)
        assert json.loads(calls[1].result)['interpretation']=='descriptive_facts_not_mastery'


@pytest.mark.parametrize('damage',['owner','step_run','call_id','name','extra_args','scope','result_endpoint','snapshot_endpoint','summary_owner','budget'])
def test_malicious_v5_tool_history_rejected_before_restore_target(tool_archive,restore_tool,tmp_path,damage):
    path,identity,account=tool_archive
    def mutate(manifest,collections):
        call=next(c for c in collections['ai_tool_calls'] if c['tool_name']=='read_note_excerpt')
        run=next(r for r in collections['ai_runs'] if r['id']==str(identity))
        if damage=='owner':call['owner_id']=str(uuid4())
        elif damage=='step_run':call['step_id']=next(s['id'] for s in collections['ai_steps'] if s['run_id']!=str(identity))
        elif damage=='call_id':next(c for c in collections['ai_tool_calls'] if c['tool_name']=='learning_summary')['provider_call_id']=call['provider_call_id']
        elif damage=='name':call['tool_name']='execute_sql'
        elif damage=='extra_args':call['arguments']['owner_id']=str(uuid4())
        elif damage=='scope':call['sources'][0]['revision']+=1
        elif damage=='result_endpoint':call['result']=json.dumps({'base_url':'https://secret.example.com','api_key':'forged-secret'})
        elif damage=='snapshot_endpoint':run['provider_snapshot']['endpoint']='https://private.example.com'
        elif damage=='summary_owner':next(c for c in collections['ai_tool_calls'] if c['tool_name']=='learning_summary')['arguments']['owner_id']=str(uuid4())
        else:call['position']=6
    broken=rewrite(path,tmp_path/(damage+'.zip'),mutate)
    verifier=restore_tool._load_verifier()
    with pytest.raises(verifier.ValidationError):verifier.verify_archive(broken)


def test_restore_terminalizes_pending_tool_and_running_paid_attempt_without_resuming(tool_archive,restore_tool,target,tmp_path):
    path,identity,account=tool_archive
    def mutate(manifest,collections):
        run=next(r for r in collections['ai_runs'] if r['id']==str(identity));run['status']='running';run['finished_at']=None
        call=deepcopy(next(c for c in collections['ai_tool_calls'] if c['tool_name']=='read_note_excerpt'));call.update(id=str(uuid4()),position=2,provider_call_id='pending_1',status='pending',result=None,sources=[],finished_at=None)
        collections['ai_tool_calls'].append(call)
        step=next(s for s in collections['ai_steps'] if s['run_id']==str(identity) and s['position']==1);step['status']='running'
    pending=rewrite(path,tmp_path/'pending.zip',mutate)
    _restore(restore_tool,pending,target)
    with target.sessions() as db:
        run=db.get(AiRun,identity);assert run.status=='interrupted' and run.lease_until is None and run.auth_session_id is None
        call=db.scalar(select(AiToolCall).where(AiToolCall.run_id==identity,AiToolCall.position==2));assert call.status=='interrupted' and call.finished_at
        step=db.scalar(select(AiStep).where(AiStep.run_id==identity,AiStep.position==1));assert step.status=='failed'
