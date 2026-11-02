from app.shared import outbound
from model_transport import install_model_transport
"""T21 real PostgreSQL with synthetic text/images and mock HTTP providers only."""
import json
from datetime import timedelta
from uuid import UUID, uuid4
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from app.modules.ai import chat_service, chat_provider
from app.modules.ai.models import AiRun, AiConversation
from app.modules.learning import quiz_schemas as s, quiz_generation, quiz_grading
from app.modules.learning.quiz_models import QuizIdentity, QuizQuestion, QuizAttempt, QuizAnswer, QuizGeneration, QuizGrading, QuizAnswerReview
from app.modules.notes.models import Note
from app.shared.config import settings
from app.shared.db import SessionLocal, utcnow
from app.shared.models import User
from app.shared.security import hasher
from conftest import login, mutate
from test_text_selection import note as text_note
from test_image_chat import mock, image_book, image_source
from test_pdf_chat import pdf_book


KINDS = ["single_choice", "multiple_choice", "true_false", "fill_blank", "short_answer"]
QUESTIONS = [
    dict(kind="single_choice", prompt="one?", options=["A", "B"], correct_answer=0, max_points=2),
    dict(kind="multiple_choice", prompt="several?", options=["A", "B", "C"], correct_answer=[0,2], max_points=3),
    dict(kind="true_false", prompt="true?", correct_answer=False, max_points=4),
    dict(kind="fill_blank", prompt="{{blank}} + {{blank}}", correct_answer=["A", "b"], max_points=5),
    dict(kind="short_answer", prompt="explain?", correct_answer="reference", explanation="rubric", max_points=6),
]


@pytest.fixture(autouse=True)
def config(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-quizzes-key")
    settings.cache_clear()
    monkeypatch.setattr(settings(), "storage_root", str(tmp_path / "private"))
    monkeypatch.setattr(outbound.httpx, "HTTPTransport", lambda: (_ for _ in ()).throw(AssertionError("No live provider")))
    monkeypatch.setattr(outbound, "resolve_public", lambda *a, **kw: ("8.8.8.8",))
    yield
    settings.cache_clear()


def quiz(client, contents=None):
    response = mutate(client, "POST", "/learning/quizzes", {"title": "Mixed test", "questions": QUESTIONS if contents is None else contents})
    assert response.status_code == 201, response.text
    return response.json()


def confirmed(client, contents=None):
    row = quiz(client, contents)
    r = mutate(client, "PATCH", "/learning/quizzes/" + row["id"], {"revision": row["revision"], "status": "confirmed"})
    assert r.status_code == 200, r.text
    return r.json()


def start(client, row):
    r = mutate(client, "POST", "/learning/quizzes/" + row["id"] + "/attempts", {"revision": row["revision"], "request_key": str(uuid4())})
    assert r.status_code == 201, r.text
    return r.json()


def save(client, attempt, index, value, **changes):
    return mutate(client, "PUT", "/learning/quiz-attempts/" + attempt["id"] + "/answers/" + attempt["questions"][index]["id"],
                  {"revision": attempt["revision"], "request_key": str(uuid4()), "answer": value, **changes})


def submit(client, attempt, **changes):
    r = mutate(client, "POST", "/learning/quiz-attempts/" + attempt["id"] + "/submit",
               {"revision": attempt["revision"], "request_key": str(uuid4()), **changes})
    assert r.status_code == 200, r.text
    return r.json()


def gen_body(client, source=None, kinds=None, count=None):
    if source is None:
        _, selection = text_note(client)
        source = {"kind": "text", **selection}
    body = {"title": "Generated", "kinds": kinds or KINDS, "count": count or len(kinds or KINDS), "source": source}
    r = mutate(client, "POST", "/learning/quiz-generations/preview", body)
    assert r.status_code == 200, r.text
    return {**body, "request_key": str(uuid4()), "preview_hash": r.json()["preview_hash"], "confirm_external": True}


def gen_create(client, body):
    r = mutate(client, "POST", "/learning/quiz-generations", body)
    assert r.status_code == 201, r.text
    return r.json()


def gen_get(client, gid):
    r = client.get("/api/v1/learning/quiz-generations/" + gid)
    assert r.status_code == 200, r.text
    return r.json()


def short_attempt(client):
    row = start(client, confirmed(client, [QUESTIONS[-1]]))
    r = save(client, row, 0, "student answer")
    assert r.status_code == 200, r.text
    return submit(client, r.json())


def grading_body(client, row):
    body = {"attempt_id": row["id"], "question_id": row["questions"][0]["id"], "revision": row["revision"]}
    r = mutate(client, "POST", "/learning/quiz-gradings/preview", body)
    assert r.status_code == 200, r.text
    return {**body, "request_key": str(uuid4()), "preview_hash": r.json()["preview_hash"], "confirm_external": True}


def grade_create(client, body):
    r = mutate(client, "POST", "/learning/quiz-gradings", body)
    assert r.status_code == 201, r.text
    return r.json()


def review(client, attempt, points=3):
    return mutate(client, "POST", "/learning/quiz-attempts/" + attempt["id"] + "/answers/" + attempt["questions"][0]["id"] + "/reviews",
        {"revision": attempt["revision"], "request_key": str(uuid4()), "points": points, "reason": "human check", "feedback": "checked"})


@pytest.mark.parametrize("change", [dict(correct_answer=True), dict(correct_answer="0"), dict(options=["A", " A "]), dict(options=[""]), dict(max_points=True), dict(max_points=0), dict(max_points=101), dict(prompt=" "), dict(extra=1)])
def test_strict_single_contract(change):
    with pytest.raises(ValidationError):
        s.QuestionContent.model_validate({**QUESTIONS[0], **change})


@pytest.mark.parametrize("index,change", [(1,dict(correct_answer=[0,0])),(1,dict(correct_answer=[False])),(1,dict(correct_answer=[])),(2,dict(correct_answer=0)),(2,dict(correct_answer="true")),(3,dict(correct_answer=["a"])),(3,dict(prompt="blank")),(3,dict(correct_answer=["a", " "])),(4,dict(correct_answer=" ")),(4,dict(options=["a","b"]))])
def test_other_kind_validation(index, change):
    with pytest.raises(ValidationError):
        s.QuestionContent.model_validate({**QUESTIONS[index], **change})


def test_manual_batch_fifty_atomic_stale_and_redaction_snapshot(client):
    login(client)
    q = confirmed(client)
    a = start(client, q)
    assert all("correct_answer" not in item and "explanation" not in item for item in a["questions"])
    assert a["max_points"] == 20
    assert a["scoring_policy"] == "exact_choices_and_boolean;fill_trim_casefold_all_or_nothing"
    original = a["questions"][0]["id"]
    r = mutate(client, "PUT", "/learning/quizzes/"+q["id"]+"/questions", {"revision":q["revision"],"questions":[QUESTIONS[0]]*50})
    assert r.status_code == 200 and r.json()["question_count"] == 50 and r.json()["status"] == "draft", r.text
    changed = r.json()
    assert mutate(client, "PUT", "/learning/quizzes/"+q["id"]+"/questions", {"revision":q["revision"],"questions":[QUESTIONS[-1]]}).status_code == 409
    assert mutate(client, "PUT", "/learning/quizzes/"+q["id"]+"/questions", {"revision":changed["revision"],"questions":[QUESTIONS[0]]*51}).status_code == 422
    reopened = client.get("/api/v1/learning/quiz-attempts/"+a["id"]).json()
    assert reopened == a and reopened["questions"][0]["id"] == original and len(reopened["questions"]) == 5
    assert save(client, a, 0, 0).status_code == 200
    assert len(client.get("/api/v1/learning/quizzes/"+q["id"]).json()["questions"]) == 50


def test_save_durable_replay_old_key_does_not_overwrite_and_local_score(client):
    login(client)
    q = confirmed(client)
    a = start(client, q)
    key = str(uuid4())
    first = save(client,a,0,0,request_key=key)
    assert first.status_code == 200, first.text
    a2 = first.json()
    assert a2["revision"] == 2
    assert save(client,a,0,1).status_code == 409
    latest = save(client,a2,0,1).json()
    replay = save(client,a,0,0,request_key=key)
    assert replay.status_code == 200 and replay.json() == a2
    assert save(client,a,0,1,request_key=key).status_code == 409
    assert client.get("/api/v1/learning/quiz-attempts/"+a["id"]).json()["answers"] == latest["answers"]
    a = latest
    for index,value in [(1,[2,0]),(2,False),(3,[" a ","B"]),(4,"explanation")]:
        r=save(client,a,index,value); assert r.status_code == 200,r.text; a=r.json()
    skey=str(uuid4())
    done=submit(client,a,request_key=skey)
    assert done["status"]=="needs_review" and done["total_points"]==12
    assert all("correct_answer" in item and "explanation" in item for item in done["questions"])
    assert submit(client,a,request_key=skey)==done
    assert save(client,done,0,0).status_code==409
    assert not chat_service.tick()


@pytest.mark.parametrize("index,value",[(0,True),(0,"0"),(0,3),(1,[0,0]),(1,[True]),(1,"0,2"),(2,0),(2,"false"),(3,["A"]),(3,[1,"b"]),(4,False)])
def test_student_strict_types(client,index,value):
    login(client)
    a=start(client,confirmed(client))
    assert save(client,a,index,value).status_code==422
    assert client.get("/api/v1/learning/quiz-attempts/"+a["id"]).json()["revision"]==1


def test_unanswered_zero_and_fill_all_or_nothing_manual_history(client):
    login(client)
    a=start(client,confirmed(client))
    a=save(client,a,3,["A","wrong"]).json()
    done=submit(client,a)
    assert done["status"]=="graded" and done["total_points"]==0
    assert review(client,done,3).status_code==422  # first question maximum is two
    r=review(client,done,2)
    assert r.status_code==200,r.text
    result=r.json()
    assert result["total_points"]==2 and len(result["reviews"])==1
    assert result["reviews"][0]["old_grade"]["points"]==0
    with SessionLocal() as db:
        rid=db.scalar(select(QuizAnswerReview.id))
    with pytest.raises(Exception):
        with SessionLocal.begin() as db:
            db.execute(text("UPDATE quiz_answer_reviews SET reason='overwrite' WHERE id=:id"),{"id":rid})


def test_owner_boundaries_and_archive_review_eligibility(client):
    login(client)
    q=confirmed(client)
    a=start(client,q)
    r=mutate(client,"POST","/learning/review-plans",{"kind":"quiz","quiz_id":q["id"],"strategy":"manual","request_key":str(uuid4())})
    assert r.status_code==201,r.text
    assert mutate(client,"PATCH","/learning/quizzes/"+q["id"],{"revision":q["revision"],"status":"archived"}).status_code==200
    assert mutate(client,"POST","/learning/quizzes/"+q["id"]+"/attempts",{"revision":q["revision"]+1,"request_key":str(uuid4())}).status_code==409
    # The active snapshot remains usable even if its author archives the quiz.
    assert save(client,a,0,0).status_code==200
    with SessionLocal.begin() as db:
        db.add(User(username="other",display_name="Other",password_hash=hasher.hash("member-password")))
    login(client,"other","member-password")
    for url in ["/learning/quizzes/"+q["id"],"/learning/quiz-attempts/"+a["id"]]:
        assert client.get("/api/v1"+url).status_code==404
    assert save(client,a,0,0).status_code==404


def test_generation_all_kinds_idempotent_hidden_and_usage(client,monkeypatch):
    login(client)
    body=gen_body(client)
    row=gen_create(client,body)
    assert gen_create(client,body)["id"]==row["id"]
    with SessionLocal() as db:
        gen=db.get(QuizGeneration,UUID(row["id"]));run=db.get(AiRun,gen.run_id);cid=str(run.conversation_id);rid=str(run.id)
        ck=str(db.get(AiConversation,run.conversation_id).request_key)
    assert client.get("/api/v1/ai/conversations").json()==[]
    assert client.get("/api/v1/ai/conversations/"+cid).status_code==404
    assert client.get("/api/v1/ai/runs/"+rid).status_code==404
    assert mutate(client,"POST","/ai/conversations",{"request_key":ck}).status_code==404
    assert mutate(client,"POST","/ai/conversations/"+cid+"/runs",{"request_key":str(uuid4()),"model":"deepseek-flash","prompt":"append"}).status_code==404
    calls=mock(monkeypatch,answer=json.dumps({"questions":QUESTIONS}))
    assert chat_service.tick()
    done=gen_get(client,row["id"])
    assert done["status"]=="succeeded" and done["usage"] and len(calls)==1,done
    result=client.get("/api/v1/learning/quizzes/"+done["quiz_id"]).json()
    assert result["status"]=="draft" and len(result["questions"])==5
    assert gen_get(client,row["id"])["quiz_id"]==done["quiz_id"]
    assert not quiz_generation.tick() and not chat_service.tick()


@pytest.mark.parametrize("payload",['oops','{"questions":[]}',json.dumps({"questions":[{**QUESTIONS[0],"source_id":"invented"}]}),json.dumps({"questions":[QUESTIONS[-1]]}),'{"questions":[],"questions":[]}',json.dumps({"questions":[{**QUESTIONS[0],"max_points":True}]} )])
def test_generation_invalid_atomic_usage(client,monkeypatch,payload):
    login(client)
    row=gen_create(client,gen_body(client,kinds=["single_choice"]))
    calls=mock(monkeypatch,answer=payload)
    assert chat_service.tick()
    done=gen_get(client,row["id"])
    assert done["status"]=="failed" and done["quiz_id"] is None and done["usage"] and len(calls)==1
    assert client.get("/api/v1/learning/quizzes").json()["total"]==0


@pytest.mark.parametrize("state",["interrupted","cancelled","failed","truncated","stale_lease","disabled","deleted","changed_source"])
def test_generation_materialization_guards_keep_usage(client,state):
    login(client)
    row=gen_create(client,gen_body(client,kinds=["single_choice"]))
    with SessionLocal.begin() as db:
        gen=db.get(QuizGeneration,UUID(row["id"]));run=db.get(AiRun,gen.run_id)
        run.status=state if state in {"interrupted","cancelled","failed","truncated"} else "succeeded"
        run.answer=json.dumps({"questions":[QUESTIONS[0]]});run.usage={"total_tokens":12};run.finished_at=utcnow()
        if state=="stale_lease":run.lease_until=utcnow()-timedelta(seconds=1)
        if state=="disabled":db.get(User,run.owner_id).status="disabled"
        if state=="deleted":db.get(Note,UUID(gen.source["note_id"])).deleted_at=utcnow()
        if state=="changed_source":run.source={**run.source,"quote":"forged"}
    assert quiz_generation.tick()
    with SessionLocal() as db:
        gen=db.get(QuizGeneration,UUID(row["id"]))
        assert gen.status=="failed" and gen.quiz_id is None
        assert db.get(AiRun,gen.run_id).usage=={"total_tokens":12}


@pytest.mark.parametrize("uncertain",[False,True])
def test_explicit_grading_local_submit_no_fee_ai_provenance_manual_override(client,monkeypatch,uncertain):
    login(client)
    a=short_attempt(client)
    assert a["status"]=="needs_review" and not chat_service.tick()
    body=grading_body(client,a)
    row=grade_create(client,body)
    assert grade_create(client,body)["id"]==row["id"]
    calls=mock(monkeypatch,answer=json.dumps({"score":4,"feedback":"suggestion","needs_review":uncertain}))
    assert chat_service.tick()
    result=client.get("/api/v1/learning/quiz-gradings/"+row["id"]).json()
    assert result["status"]=="succeeded" and result["usage"] and len(calls)==1,result
    sent=str(calls[0])
    assert "student answer" in sent and "reference" in sent and "image_url" not in sent
    a=client.get("/api/v1/learning/quiz-attempts/"+a["id"]).json()
    assert a["status"]==("needs_review" if uncertain else "graded")
    assert a["answers"][0]["provenance"]==("ai_suggestion" if uncertain else "ai")
    assert a["total_points"]==(0 if uncertain else 4)
    revised=review(client,a,5)
    assert revised.status_code==200 and revised.json()["total_points"]==5
    assert revised.json()["answers"][0]["provenance"]=="manual"
    assert not quiz_grading.tick() and not chat_service.tick()


@pytest.mark.parametrize("result",[{"score":7,"feedback":"x","needs_review":False},{"score":True,"feedback":"x","needs_review":False},{"score":"3","feedback":"x","needs_review":False},{"score":3,"feedback":"x","needs_review":"false"},{"score":3,"feedback":"x","needs_review":False,"source":"invented"},{"score":float("nan"),"feedback":"x","needs_review":False}])
def test_grading_strict_invalid_keeps_usage(client,monkeypatch,result):
    login(client)
    a=short_attempt(client);row=grade_create(client,grading_body(client,a))
    calls=mock(monkeypatch,answer=json.dumps(result))
    assert chat_service.tick()
    done=client.get("/api/v1/learning/quiz-gradings/"+row["id"]).json()
    assert done["status"]=="failed" and done["usage"] and len(calls)==1
    assert client.get("/api/v1/learning/quiz-attempts/"+a["id"]).json()["revision"]==a["revision"]


def test_grading_stale_before_post_and_after_paid_call(client,monkeypatch):
    login(client)
    a=short_attempt(client);row=grade_create(client,grading_body(client,a))
    r=review(client,a,2);assert r.status_code==200,r.text
    calls=mock(monkeypatch,answer=json.dumps({"score":5,"feedback":"old","needs_review":False}))
    assert chat_service.tick()
    assert len(calls)==0
    done=client.get("/api/v1/learning/quiz-gradings/"+row["id"]).json()
    assert done["status"]=="failed" and done["error_code"]=="quiz_grading_stale"
    a=r.json();row=grade_create(client,grading_body(client,a))
    with SessionLocal.begin() as db:
        g=db.get(QuizGrading,UUID(row["id"]));run=db.get(AiRun,g.run_id)
        run.status="succeeded";run.answer=json.dumps({"score":5,"feedback":"old","needs_review":False});run.usage={"total_tokens":7};run.finished_at=utcnow()
    assert review(client,a,3).status_code==200
    done=client.get("/api/v1/learning/quiz-gradings/"+row["id"]).json()
    assert done["status"]=="failed" and done["usage"]=={"total_tokens":7}
    assert client.get("/api/v1/learning/quiz-attempts/"+a["id"]).json()["total_points"]==3


@pytest.mark.parametrize("pdf",[False,True])
def test_visual_generation_strict_prompt_and_frozen_source(client,monkeypatch,pdf):
    login(client)
    if pdf:
        nid=pdf_book(client);detail=client.get("/api/v1/ai/pdf-notebooks/"+nid).json()
        source={"kind":"pdf","note_id":nid,"revision":detail["revision"],"mode":"pages","page_numbers":[1]}
    else:
        nid=image_book(client);source={"kind":"image",**image_source(client,nid)}
    row=gen_create(client,gen_body(client,source=source,kinds=["single_choice"]))
    calls=mock(monkeypatch,answer=json.dumps({"questions":[QUESTIONS[0]]}))
    assert chat_service.tick()
    done=gen_get(client,row["id"])
    assert done["status"]=="succeeded",done
    assert len(calls)==1 and "image_url" in str(calls[0]) and "引用使用" not in str(calls[0])
    q=client.get("/api/v1/learning/quizzes/"+done["quiz_id"]).json()
    r=client.get("/api/v1/learning/quizzes/"+q["id"]+"/questions/"+q["questions"][0]["id"]+"/source")
    assert r.status_code==200,r.text



def test_parallel_autosaves_same_revision_only_one_wins(client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.modules.learning import quiz_service
    from app.shared.errors import AppError
    login(client)
    a=start(client,confirmed(client,[QUESTIONS[0]]))
    with SessionLocal() as db:
        owner=db.scalar(select(User.id).where(User.username=="admin"))
    gate=Barrier(2)
    def attempt(value):
        with SessionLocal() as db:
            gate.wait()
            try:
                result=quiz_service.save_answer(db,owner,UUID(a["id"]),UUID(a["questions"][0]["id"]),s.AnswerSave(revision=1,request_key=uuid4(),answer=value))
                return 200,result.answers[0].answer
            except AppError as exc:
                db.rollback()
                return exc.status,None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(attempt,[0,1]))
    assert sorted(v[0] for v in outcomes)==[200,409]
    latest=client.get("/api/v1/learning/quiz-attempts/"+a["id"]).json()
    assert latest["revision"]==2
    assert latest["answers"][0]["answer"]==next(v[1] for v in outcomes if v[0]==200)


def test_nul_student_answer_controlled_and_large_valid_reference_grading(client):
    login(client)
    a=start(client,confirmed(client,[{**QUESTIONS[-1],"correct_answer":"a"*7900}]))
    assert save(client,a,0,"x\x00").status_code==422
    a=save(client,a,0,"student answer").json()
    a=submit(client,a)
    # User material exceeds the ordinary 8000-character chat prompt limit;
    # grading uses the independent bounded hidden input, not a public RunCreate payload.
    row=grade_create(client,grading_body(client,a))
    assert row["status"]=="pending"


def test_empty_draft_confirm_preview_constraints_and_batch_source_atomicity(client):
    login(client)
    q=quiz(client,[])
    assert mutate(client,"PATCH","/learning/quizzes/"+q["id"],{"revision":1,"status":"confirmed"}).status_code==422
    q=quiz(client,[QUESTIONS[0]])
    original=q["questions"][0]["id"]
    _,selection=text_note(client)
    bad={**selection,"note_id":str(uuid4())}
    r=mutate(client,"PUT","/learning/quizzes/"+q["id"]+"/questions",{"revision":q["revision"],"questions":[QUESTIONS[1],{**QUESTIONS[-1],"source":{"kind":"text",**bad}}]})
    assert r.status_code in {404,409},r.text
    retained=client.get("/api/v1/learning/quizzes/"+q["id"]).json()
    assert retained["revision"]==q["revision"] and retained["questions"][0]["id"]==original
    body=gen_body(client)
    for changes in [{"confirm_external":1},{"confirm_external":False},{"count":True},{"count":21},{"count":1},{"kinds":["single_choice","single_choice"]},{"preview_hash":"f"*64}]:
        assert mutate(client,"POST","/learning/quiz-generations",{**body,**changes}).status_code in {422,409}
    assert client.get("/api/v1/learning/quiz-generations").json()["total"]==0


def test_unknown_fee_never_replay_or_status_post(client,monkeypatch):
    import httpx
    login(client)
    body=gen_body(client,kinds=["single_choice"]);row=gen_create(client,body)
    def lost(_):
        raise httpx.ReadTimeout("uncertain simulated response")
    calls=mock(monkeypatch,handler=lost)
    # Status reads before a worker execution never issue a network call.
    assert gen_get(client,row["id"])["status"]=="pending" and not calls
    assert chat_service.tick()
    done=gen_get(client,row["id"])
    assert done["status"]=="failed" and len(calls)==1
    assert gen_create(client,body)["id"]==row["id"]
    assert not chat_service.tick() and len(calls)==1


def test_quiz_historical_text_source_preserved_and_invalid_source_not_deleted(client):
    from test_text_selection import document
    login(client)
    original,selection=text_note(client,"historical material")
    q=confirmed(client,[{**QUESTIONS[0],"source":{"kind":"text",**selection}}])
    a=start(client,q)
    r=mutate(client,"PATCH","/notes/"+original["id"],{"revision":original["revision"],"title":"changed","body_json":document("new material")})
    assert r.status_code==200,r.text
    url="/api/v1/learning/quiz-attempts/"+a["id"]+"/questions/"+a["questions"][0]["id"]+"/source"
    r=client.get(url)
    assert r.status_code==200 and r.json()["text"]=="historical material" and r.json()["source_changed"]
    with SessionLocal.begin() as db:
        db.get(Note,UUID(original["id"])).deleted_at=utcnow()
    assert client.get(url).status_code==409
    retained=client.get("/api/v1/learning/quizzes/"+q["id"]).json()
    assert not retained["questions"][0]["source_valid"] and retained["question_count"]==1
    assert save(client,a,0,0).status_code==200


@pytest.mark.parametrize("state",["interrupted","stale_lease","disabled"])
def test_grading_terminal_guards_and_known_usage(client,state):
    login(client)
    a=short_attempt(client);row=grade_create(client,grading_body(client,a))
    with SessionLocal.begin() as db:
        g=db.get(QuizGrading,UUID(row["id"]));run=db.get(AiRun,g.run_id)
        run.status="interrupted" if state=="interrupted" else "succeeded"
        run.answer=json.dumps({"score":5,"feedback":"old","needs_review":False})
        run.finished_at=utcnow();run.usage={"total_tokens":13}
        if state=="stale_lease":run.lease_until=utcnow()-timedelta(seconds=1)
        if state=="disabled":db.get(User,run.owner_id).status="disabled"
    assert quiz_grading.tick()
    with SessionLocal() as db:
        g=db.get(QuizGrading,UUID(row["id"]))
        assert g.status=="failed" and db.get(AiRun,g.run_id).usage=={"total_tokens":13}
        assert db.get(QuizAttempt,UUID(a["id"])).revision==a["revision"]



def test_start_revision_stale_and_fixed_durable_receipt_after_submit(client):
    login(client)
    q=confirmed(client,[QUESTIONS[0]])
    body={"revision":q["revision"],"request_key":str(uuid4())}
    url="/learning/quizzes/"+q["id"]+"/attempts"
    first=mutate(client,"POST",url,body)
    assert first.status_code==201,first.text
    original=first.json()
    saved=save(client,original,0,0)
    assert saved.status_code==200,saved.text
    done=submit(client,saved.json())
    assert "correct_answer" in done["questions"][0]
    replay=mutate(client,"POST",url,body)
    assert replay.status_code==201 and replay.json()==original
    assert "correct_answer" not in replay.json()["questions"][0]
    # Current quiz metadata cannot change an already accepted start receipt.
    newer=mutate(client,"PATCH","/learning/quizzes/"+q["id"],{"revision":q["revision"],"questions":[QUESTIONS[-1]],"status":"confirmed"})
    assert newer.status_code==200,newer.text
    assert mutate(client,"POST",url,body).json()==original
    assert mutate(client,"POST",url,{**body,"revision":newer.json()["revision"]}).status_code==409
    assert mutate(client,"POST",url,{**body,"request_key":str(uuid4())}).status_code==409
    fresh=mutate(client,"POST",url,{"revision":newer.json()["revision"],"request_key":str(uuid4())})
    assert fresh.status_code==201 and fresh.json()["quiz_revision"]==newer.json()["revision"]



def test_review_history_links_same_text_reviews_to_distinct_question_answers(client):
    login(client)
    row=confirmed(client,[QUESTIONS[-1],QUESTIONS[-1]])
    attempt=start(client,row)
    for index in range(2):
        saved=save(client,attempt,index,"identical student answer")
        assert saved.status_code==200,saved.text
        attempt=saved.json()
    attempt=submit(client,attempt)
    for question in attempt["questions"]:
        result=mutate(client,"POST","/learning/quiz-attempts/"+attempt["id"]+"/answers/"+question["id"]+"/reviews",
            {"revision":attempt["revision"],"request_key":str(uuid4()),"points":3,"reason":"same reason","feedback":"same feedback"})
        assert result.status_code==200,result.text
        attempt=result.json()
    reloaded=client.get("/api/v1/learning/quiz-attempts/"+attempt["id"])
    assert reloaded.status_code==200,reloaded.text
    dto=reloaded.json()
    answers_by_id={answer["id"]:answer for answer in dto["answers"]}
    assert len(answers_by_id)==2 and len(dto["reviews"])==2
    reviewed_questions=[answers_by_id[review["answer_id"]]["question_id"] for review in dto["reviews"]]
    assert set(reviewed_questions)=={question["id"] for question in dto["questions"]}
    assert len(set(reviewed_questions))==2
    assert all(review["reason"]=="same reason" and review["new_grade"]["points"]==3 for review in dto["reviews"])
