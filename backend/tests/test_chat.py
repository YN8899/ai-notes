import hashlib
from contextlib import ExitStack
import io
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from sqlalchemy import select
from PIL import Image
from app.main import app
from app.shared.db import SessionLocal
from app.shared.models import User
from app.shared.security import hasher
from app.modules.social.models import Friendship
from app.modules.social.chat_models import ChatAttachment, ChatMessage
from conftest import login, mutate


@pytest.fixture
def people(tmp_path, monkeypatch):
    from app.shared.config import settings
    monkeypatch.setattr(settings(), "storage_root", str(tmp_path))
    password = hasher.hash("member-password")
    with SessionLocal.begin() as db:
        users = [User(username=name, display_name=name, password_hash=password) for name in ("alice", "bob", "carol")]
        db.add_all(users)
        db.flush()
        ids = [u.id for u in users]
        low, high = sorted(ids[:2])
        db.add(Friendship(user_low_id=low, user_high_id=high, requester_id=ids[0], status="accepted"))
    # Lifespan owns LISTEN threads; close every independent TestClient loop.
    with ExitStack() as stack:
        clients = [stack.enter_context(TestClient(app)) for _ in ids]
        for c, name in zip(clients, ("alice", "bob", "carol")):
            login(c, name, "member-password")
        yield clients, ids


def make_thread(people):
    clients, ids = people
    result = mutate(clients[0], "POST", "/social/chat/threads", {"user_id": str(ids[1])})
    assert result.status_code == 201, result.text
    return result.json()["id"]


def send(c, tid, key="message-1", body="你好", attachments=None):
    return mutate(c, "POST", f"/social/chat/threads/{tid}/messages", {"client_message_id": key, "body": body, "attachment_ids": attachments or []})


def test_thread_pair_unique_and_nonfriends_denied(people):
    cs, ids = people
    tid = make_thread(people)
    assert mutate(cs[1], "POST", "/social/chat/threads", {"user_id": str(ids[0])}).json()["id"] == tid
    assert mutate(cs[0], "POST", "/social/chat/threads", {"user_id": str(ids[2])}).status_code == 403
    assert cs[2].get(f"/api/v1/social/chat/threads/{tid}").status_code == 404
    assert cs[2].get(f"/api/v1/social/chat/threads/{tid}/messages").status_code == 404
    assert send(cs[2], tid).status_code == 404


def test_messages_persist_dedupe_conflict_and_paginate(people):
    cs, ids = people
    tid = make_thread(people)
    first = send(cs[0], tid).json()
    assert send(cs[0], tid).json()["id"] == first["id"]
    assert send(cs[0], tid, body="不同").status_code == 409
    second = send(cs[1], tid, "reply", "收到").json()
    assert [first["seq"], second["seq"]] == [1, 2]
    page = cs[1].get(f"/api/v1/social/chat/threads/{tid}/messages?limit=1").json()
    assert page["has_more"] and page["next_after"] == 1
    assert cs[1].get(f"/api/v1/social/chat/threads/{tid}/messages?after_seq=1").json()["items"][0]["id"] == second["id"]
    with SessionLocal() as db:
        assert len(db.scalars(select(ChatMessage)).all()) == 2


def test_read_monotonic_and_unread_ignores_self(people):
    cs, _ = people
    tid = make_thread(people)
    send(cs[0], tid)
    send(cs[1], tid, "reply")
    assert cs[1].get(f"/api/v1/social/chat/threads/{tid}").json()["unread"] == 1
    path = f"/social/chat/threads/{tid}/read"
    assert mutate(cs[1], "POST", path, {"seq": 3}).status_code == 409
    assert mutate(cs[1], "POST", path, {"seq": 2}).json()["last_read_seq"] == 2
    assert mutate(cs[1], "POST", path, {"seq": 1}).json()["last_read_seq"] == 2
    assert cs[1].get(f"/api/v1/social/chat/threads/{tid}").json()["unread"] == 0
    assert cs[0].get(f"/api/v1/social/chat/threads/{tid}").json()["peer_read_seq"] == 2
    assert cs[0].get('/api/v1/social/chat/summary').json() == {'threads': 1, 'unread_messages': 1, 'unread_threads': 1}
    assert cs[1].get('/api/v1/social/chat/summary').json() == {'threads': 1, 'unread_messages': 0, 'unread_threads': 0}


def test_removed_friend_retains_history_but_cannot_send(people):
    cs, ids = people
    tid = make_thread(people)
    send(cs[0], tid)
    assert mutate(cs[0], "DELETE", f"/social/friends/{ids[1]}", {}).status_code == 204
    assert send(cs[1], tid, "after-remove").status_code == 403
    assert len(cs[1].get(f"/api/v1/social/chat/threads/{tid}/messages").json()["items"]) == 1
    assert cs[1].get(f"/api/v1/social/chat/threads/{tid}").json()["can_send"] is False


@pytest.mark.parametrize("body", ["", "\x00"])
def test_bad_message_rejected(people, body):
    assert send(people[0][0], make_thread(people), body=body).status_code == 422


def test_invalid_unicode_json_rejected(people):
    import json
    c = people[0][0]
    tid = make_thread(people)
    csrf = c.get("/api/v1/auth/csrf").json()["csrf_token"]
    response = c.post(f"/api/v1/social/chat/threads/{tid}/messages", content=json.dumps({"client_message_id": "unicode", "body": "\ud800"}), headers={"Origin": "http://testserver", "X-CSRF-Token": csrf, "Content-Type": "application/json"})
    assert response.status_code == 422


def upload(c, tid, data=b"hello", filename="notes.txt", key="upload-1"):
    csrf = c.get("/api/v1/auth/csrf").json()["csrf_token"]
    return c.put(f"/api/v1/social/chat/threads/{tid}/attachments", params={"filename": filename, "request_key": key}, content=data, headers={"Origin": "http://testserver", "X-CSRF-Token": csrf})


def test_attachment_private_until_sent_and_immutable(people):
    cs, _ = people
    tid = make_thread(people)
    response = upload(cs[0], tid)
    assert response.status_code == 201, response.text
    aid = response.json()["id"]
    path = f"/api/v1/social/chat/attachments/{aid}/content"
    assert cs[1].get(path).status_code == 404
    assert cs[2].get(path).status_code == 404
    assert upload(cs[0], tid).json()["id"] == aid
    assert upload(cs[0], tid, data=b"changed").status_code == 409
    assert send(cs[1], tid, attachments=[aid]).status_code == 409
    r = send(cs[0], tid, attachments=[aid])
    assert r.status_code == 201, r.text
    assert cs[1].get(path).content == b"hello"
    assert cs[2].get(path).status_code == 404
    assert send(cs[0], tid, key="reuse-attachment", attachments=[aid]).status_code == 409
    assert send(cs[0], tid, attachments=[aid]).json()["id"] == r.json()["id"]
    with SessionLocal.begin() as db:
        attachment = db.get(ChatAttachment, aid)
        from app.modules.files.service import storage
        storage().put(attachment.object_key, b"HELLO")
    assert cs[1].get(path).status_code == 503


@pytest.mark.parametrize("data,name,status", [(b"", "empty.txt", 413), (b"fake", "image.png", 422), (b"<script>", "unsafe.html", 422), (b"hello", "../bad.txt", 422), (b"bad", "a.pdf", 422), (b"\x00", "a.txt", 422)])
def test_attachment_validation(people, data, name, status):
    assert upload(people[0][0], make_thread(people), data=data, filename=name).status_code == status


def test_valid_image_attachment(people):
    out = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(out, format="PNG")
    r = upload(people[0][0], make_thread(people), data=out.getvalue(), filename="图片.png")
    assert r.status_code == 201 and r.json()["mime"] == "image/png"


def test_truncated_jpeg_rejected_after_header_verification(people):
    out = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(out, format="JPEG")
    assert upload(people[0][0], make_thread(people), data=out.getvalue()[:-10], filename="truncated.jpg").status_code == 422


def test_simultaneous_upload_and_peer_send_do_not_reverse_locks(people):
    cs, _ = people
    tid = make_thread(people)
    with ThreadPoolExecutor(max_workers=2) as pool:
        uploaded = pool.submit(upload, cs[0], tid)
        sent = pool.submit(send, cs[1], tid, "peer-send")
        assert uploaded.result(timeout=15).status_code == 201
        assert sent.result(timeout=15).status_code == 201


def test_concurrent_opposite_sends_have_unique_sequence(people):
    cs, _ = people
    tid = make_thread(people)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: send(pair[0], tid, pair[1]), [(cs[0], "a"), (cs[1], "b")]))
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    assert sorted(r.json()["seq"] for r in results) == [1, 2]


def ticket(c):
    r = mutate(c, "POST", "/social/realtime-ticket", {})
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


def test_websocket_origin_one_use_and_durable_changes(people):
    cs, _ = people
    tid = make_thread(people)
    raw = ticket(cs[1])
    with pytest.raises(WebSocketDisconnect):
        with cs[1].websocket_connect("/api/v1/realtime/ws", headers={"Origin": "http://evil"}):
            pass
    with cs[1].websocket_connect("/api/v1/realtime/ws", headers={"Origin": "http://testserver"}) as ws:
        ws.send_json({"ticket": raw})
        assert ws.receive_json()["type"] == "ready"
        assert send(cs[0], tid).status_code == 201
        ws.send_text("ping")
        event = ws.receive_json()
        assert event["type"] == "changed" and "chat" in event["domains"]
        assert event["threads"] == {}  # cursors/data are authoritative HTTP facts
        page = cs[1].get(f"/api/v1/social/chat/threads/{tid}/messages?after_seq=0").json()
        assert page["items"][0]["seq"] == 1
        assert "你好" not in str(event)
    with pytest.raises(WebSocketDisconnect):
        with cs[1].websocket_connect("/api/v1/realtime/ws", headers={"Origin": "http://testserver"}) as ws:
            ws.send_json({"ticket": raw})
            ws.receive_json()


def test_socket_closes_after_logout(people):
    c = people[0][0]
    raw = ticket(c)
    with c.websocket_connect("/api/v1/realtime/ws", headers={"Origin": "http://testserver"}) as ws:
        ws.send_json({"ticket": raw})
        assert ws.receive_json()["type"] == "ready"
        assert mutate(c, "POST", "/auth/logout", {}).status_code == 200
        ws.send_text("ping")
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


@pytest.mark.parametrize("authenticated", [False, True])
def test_binary_socket_frames_close_cleanly(people, authenticated):
    c = people[0][0]
    raw = ticket(c)
    with c.websocket_connect("/api/v1/realtime/ws", headers={"Origin": "http://testserver"}) as ws:
        if authenticated:
            ws.send_json({"ticket": raw})
            assert ws.receive_json()["type"] == "ready"
        ws.send_bytes(b"binary-is-not-a-text-protocol")
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_worker_maintenance_cleans_abandoned_tickets_before_old_sessions(people):
    from datetime import timedelta
    from app.shared.db import utcnow
    from app.shared.models import AuthSession
    from app.modules.social.chat_models import RealtimeTicket
    from app.modules.jobs.service import maintenance
    c=people[0][0]
    ticket(c)
    with SessionLocal.begin() as db:
        row=db.scalar(select(RealtimeTicket))
        session_id=row.session_id
        db.get(AuthSession,session_id).expires_at=utcnow()-timedelta(days=8)
    maintenance()
    with SessionLocal() as db:
        assert db.get(AuthSession,session_id) is None
        assert db.scalar(select(RealtimeTicket)) is None


def test_realtime_contract_preserves_existing_file_ticket_schema_name():
    # Regression: using TicketOut for both domains made OpenAPI qualify both
    # names, breaking the existing generated TypeScript FileTicket alias.
    schema=app.openapi()
    assert 'TicketOut' in schema['components']['schemas']
    assert 'RealtimeTicketOut' in schema['components']['schemas']
    old=schema['paths']['/api/v1/files/{file_id}/ticket']['post']['responses']['200']['content']['application/json']['schema']
    new=schema['paths']['/api/v1/social/realtime-ticket']['post']['responses']['200']['content']['application/json']['schema']
    assert old['$ref']=='#/components/schemas/TicketOut'
    assert new['$ref']=='#/components/schemas/RealtimeTicketOut'
