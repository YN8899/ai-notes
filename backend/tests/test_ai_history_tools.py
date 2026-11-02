"""Historical tool data remains readable but must be revalidated before reuse."""
import json
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select

from app.modules.ai import chat_service
from app.modules.ai.models import AiRun, AiStep, AiToolCall
from app.modules.notes.models import Note
from app.shared import outbound
from app.shared.config import settings
from app.shared.db import SessionLocal, utcnow
from app.shared.models import AuthSession
from conftest import login, mutate
from model_transport import install_model_transport
from test_ai_chat import conversation, read, stream_bytes
from test_ai_compatible import config, note, submit, tool_response, MODEL, profile

PRIVATE = "PRIVATE_HISTORICAL_TOOL_EXCERPT"
ERROR = "history_tool_source_unavailable"


def completed_tool_turn(client, monkeypatch, kind):
    login(client)
    source = note(client, text=PRIVATE)
    cid = conversation(client)
    if kind == "summary_facts":
        names = ["learning_summary"]
        scope = {"learning_summary": {"date_from": "2026-11-01", "date_to": "2026-11-01"}}
        name, args = "learning_summary", "{}"
    else:
        names = ["read_note_excerpt"]
        scope = {"note_ids": [source["id"]]}
        name, args = "read_note_excerpt", json.dumps({"note_id": source["id"]})
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return tool_response(name, args)
        if len(requests) == 2:
            assert any(message["role"] == "tool" for message in payload["messages"])
        return httpx.Response(200, content=stream_bytes(PRIVATE))

    install_model_transport(monkeypatch, handler)
    response = submit(client, cid, provider="custom", model=MODEL, tools=names, tool_scope=scope)
    assert response.status_code == 201, response.text
    first = response.json()
    assert chat_service.tick()
    assert read(client, first)["status"] == "succeeded" and len(requests) == 2
    return source, cid, first, requests


def invalidate(client, source, kind):
    if kind == "summary_facts":
        note(client, title="Aggregate changes", text="another note")
    elif kind == "trash":
        response = mutate(client, "DELETE", "/notes/" + source["id"], {"revision": source["revision"]})
        assert response.status_code == 204, response.text
    else:
        with SessionLocal.begin() as db:
            db.get(Note, UUID(source["id"])).revision += 1


@pytest.mark.parametrize("kind", ["revision", "trash", "summary_facts"])
@pytest.mark.parametrize("when", ["before_create", "after_create", "final_gate"])
def test_stale_historical_tools_never_reach_next_post(client, monkeypatch, kind, when):
    source, cid, first, requests = completed_tool_turn(client, monkeypatch, kind)
    if when == "before_create":
        invalidate(client, source, kind)
    response = submit(client, cid, prompt="继续说明", previous_run_id=first["id"], tools=[])
    if when == "before_create":
        assert response.status_code == 409, response.text
        assert response.json()["code"] == ERROR
        assert "新建对话" in response.json()["message"]
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(AiRun)) == 1
    else:
        assert response.status_code == 201, response.text
        second = response.json()
        if when == "after_create":
            invalidate(client, source, kind)
        else:
            # The durable step and request are already prepared when DNS runs;
            # mutation here must still be caught by the final before_send gate.
            def resolve_after_change(*args, **kwargs):
                with SessionLocal() as db:
                    step = db.scalar(select(AiStep).where(AiStep.run_id == UUID(second["id"])))
                    assert step is not None and step.status == "running"
                invalidate(client, source, kind)
                return ("8.8.8.8",)
            monkeypatch.setattr(outbound, "resolve_public", resolve_after_change)
        assert chat_service.tick()
        current = read(client, second)
        assert current["status"] == "failed" and current["error_code"] == ERROR
        with SessionLocal() as db:
            steps = list(db.scalars(select(AiStep).where(AiStep.run_id == UUID(second["id"]))))
            assert len(steps) == (1 if when == "final_gate" else 0)
            assert all(step.status == "failed" and step.error_code == ERROR for step in steps)
            assert not db.scalar(select(AiToolCall.id).where(AiToolCall.run_id == UUID(second["id"])))
        assert not chat_service.tick()  # Terminal failure never queues a paid retry.
    assert len(requests) == 2
    # Readable history/provenance remains independent of source/config validity.
    assert read(client, first)["status"] == "succeeded"
    assert client.get("/api/v1/ai/runs/" + first["id"] + "/tools").status_code == 200
    assert client.get("/api/v1/ai/conversations/" + cid).status_code == 200


@pytest.mark.parametrize("kind", ["revision", "summary_facts"])
@pytest.mark.parametrize("old_state", ["unchanged", "provider_disabled", "provider_removed", "session_revoked"])
def test_valid_history_allows_plain_followup_independent_of_old_credentials(client, monkeypatch, kind, old_state):
    _, cid, first, requests = completed_tool_turn(client, monkeypatch, kind)
    with SessionLocal() as db:
        old_session = db.get(AiRun, UUID(first["id"])).auth_session_id
    # Current authorization comes from a new active session, never the old run.
    login(client)
    if old_state == "session_revoked":
        with SessionLocal.begin() as db:
            db.get(AuthSession, old_session).revoked_at = utcnow()
    elif old_state in {"provider_disabled", "provider_removed"}:
        profiles = [] if old_state == "provider_removed" else [profile(enabled=False)]
        monkeypatch.setenv("AI_PROVIDER_PROFILES", json.dumps(profiles))
        settings.cache_clear()
    response = submit(client, cid, prompt="继续说明", previous_run_id=first["id"], tools=[])
    assert response.status_code == 201, response.text
    second = response.json()
    with SessionLocal() as db:
        assert db.get(AiRun, UUID(second["id"])).auth_session_id != old_session
    assert chat_service.tick()
    assert read(client, second)["status"] == "succeeded"
    assert len(requests) == 3
    assert any(m["role"] == "assistant" and m["content"] == PRIVATE for m in requests[2]["messages"])
    assert "tools" not in requests[2] and requests[2]["model"] == "deepseek-flash"
    assert read(client, first)["status"] == "succeeded"


@pytest.mark.parametrize("kind", ["revision", "summary_facts"])
def test_tool_scope_is_checked_beyond_immediately_previous_plain_turn(client, monkeypatch, kind):
    source, cid, first, requests = completed_tool_turn(client, monkeypatch, kind)
    second = submit(client, cid, previous_run_id=first["id"], tools=[])
    assert second.status_code == 201, second.text
    assert chat_service.tick() and read(client, second.json())["status"] == "succeeded"
    invalidate(client, source, kind)
    third = submit(client, cid, previous_run_id=second.json()["id"], tools=[])
    assert third.status_code == 409 and third.json()["code"] == ERROR
    assert len(requests) == 3


def test_historical_scope_rechecked_between_new_turns_model_and_tool(client, monkeypatch):
    source, cid, first, requests = completed_tool_turn(client, monkeypatch, "revision")
    current_source = note(client, text="Different explicitly confirmed scope")

    def handler(request):
        requests.append(json.loads(request.content))
        assert len(requests) == 3
        invalidate(client, source, "revision")
        return tool_response("read_note_excerpt", json.dumps({"note_id": current_source["id"]}))

    install_model_transport(monkeypatch, handler)
    response = submit(client, cid, previous_run_id=first["id"], provider="custom", model=MODEL,
        tools=["read_note_excerpt"], tool_scope={"note_ids": [current_source["id"]]})
    assert response.status_code == 201, response.text
    assert chat_service.tick()
    current = read(client, response.json())
    assert current["status"] == "failed" and current["error_code"] == ERROR
    with SessionLocal() as db:
        steps = list(db.scalars(select(AiStep).where(AiStep.run_id == UUID(current["id"]))))
        # The completed paid attempt is retained, but no tool or extra POST runs.
        assert len(steps) == 1 and steps[0].status == "succeeded"
        assert not db.scalar(select(AiToolCall.id).where(AiToolCall.run_id == UUID(current["id"])))
    assert not chat_service.tick() and len(requests) == 3
