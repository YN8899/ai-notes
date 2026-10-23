from datetime import timedelta

import pytest
from sqlalchemy import select

from app.shared.db import SessionLocal, utcnow
from app.shared.models import AuthSession
from app.shared.security import digest
from conftest import invite, login, mutate, register
from test_files import png


def tab_login(client, username="admin", password="admin-test-password"):
    response = client.post("/api/v1/auth/tab/login", headers={"Origin": "http://testserver", "X-Notes-Client": "tab"}, json={
        "username": username, "password": password})
    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def headers(session):
    return {"Authorization": "Bearer " + session["session_token"],
            "X-CSRF-Token": session["csrf_token"], "Origin": "http://testserver"}


def create_members(client):
    code = invite(client, 2)["code"]
    assert register(client, code, "alice").status_code == 201
    assert register(client, code, "bob").status_code == 201


def test_tab_sessions_ignore_shared_cookie_and_logout_independently(client):
    create_members(client)
    alice, bob = tab_login(client, "alice", "member-password"), tab_login(client, "bob", "member-password")
    # A different identity in the shared Cookie jar must never override the tab.
    login(client)
    assert client.get("/api/v1/auth/me").json()["username"] == "admin"
    for session, name in ((alice, "alice"), (bob, "bob")):
        assert client.get("/api/v1/auth/me", headers=headers(session)).json()["username"] == name
    response = client.post("/api/v1/auth/logout", headers=headers(alice), json={})
    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    assert client.get("/api/v1/auth/me", headers=headers(alice)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=headers(bob)).json()["username"] == "bob"
    assert client.get("/api/v1/auth/me").json()["username"] == "admin"
    # Two separate logins of the same account also remain independent.
    second = tab_login(client, "bob", "member-password")
    assert second["session_token"] != bob["session_token"]
    assert client.post("/api/v1/auth/logout", headers=headers(second), json={}).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers(bob)).status_code == 200


@pytest.mark.parametrize("authorization", ["", "Bearer", "Basic abc", "Bearer bad", "Bearer a b"])
def test_bad_authorization_never_falls_back_to_cookie(client, authorization):
    login(client)
    assert client.get("/api/v1/auth/me", headers={"Authorization": authorization}).status_code == 401


def test_tab_registration_preserves_cookie_and_uses_hashed_credentials(client):
    code = invite(client)["code"]
    response = client.post("/api/v1/auth/tab/register", headers={"Origin": "http://testserver", "X-Notes-Client": "tab"}, json={
        "username": "alice", "password": "member-password", "display_name": "Alice", "invite_code": code})
    assert response.status_code == 201
    assert "set-cookie" not in response.headers
    session = response.json()
    assert session["user"]["username"] == "alice"
    assert client.get("/api/v1/auth/me").json()["username"] == "admin"
    assert client.get("/api/v1/auth/me", headers=headers(session)).json()["username"] == "alice"
    with SessionLocal() as db:
        stored = db.scalar(select(AuthSession).where(AuthSession.token_hash == digest(session["session_token"])))
        assert stored and stored.csrf_hash == digest(session["csrf_token"])


def test_tab_login_and_mutations_keep_origin_and_csrf_guards(client):
    assert client.post("/api/v1/auth/tab/login", json={"username": "admin", "password": "admin-test-password"}).status_code == 403
    assert client.post("/api/v1/auth/tab/login", headers={"Origin": "http://testserver"}, json={"username": "admin", "password": "admin-test-password"}).status_code == 403
    assert client.post("/api/v1/auth/tab/login", headers={"Origin": "http://evil.test", "X-Notes-Client": "tab"}, json={"username": "admin", "password": "admin-test-password"}).status_code == 403
    first, second = tab_login(client), tab_login(client)
    wrong = headers(first) | {"X-CSRF-Token": second["csrf_token"]}
    assert client.post("/api/v1/notes", headers=wrong, json={"title": "Blocked", "kind": "text"}).status_code == 403
    assert client.post("/api/v1/notes", headers=headers(first) | {"Origin": "http://evil.test"}, json={"title": "Blocked", "kind": "text"}).status_code == 403
    assert client.post("/api/v1/notes", headers=headers(first), json={"title": "Allowed", "kind": "text"}).status_code == 201


def test_tab_session_expiry_disabling_and_password_reset(client):
    create_members(client)
    alice, bob = tab_login(client, "alice", "member-password"), tab_login(client, "bob", "member-password")
    with SessionLocal.begin() as db:
        db.scalar(select(AuthSession).where(AuthSession.token_hash == digest(alice["session_token"]))).expires_at = utcnow() - timedelta(seconds=1)
    assert client.get("/api/v1/auth/me", headers=headers(alice)).status_code == 401
    alice = tab_login(client, "alice", "member-password")
    login(client)
    assert mutate(client, "PATCH", "/admin/users/" + alice["user"]["id"], {"status": "disabled"}).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers(alice)).status_code == 401
    reset = mutate(client, "POST", "/admin/users/" + bob["user"]["id"] + "/reset-token", {}).json()
    assert client.post("/api/v1/auth/tab/password/reset", headers={"Origin": "http://testserver", "X-Notes-Client": "tab"}, json={"token": reset["token"], "password": "replacement-password"}).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers(bob)).status_code == 401


def test_tab_private_notes_upload_and_image_read(client, tmp_path, monkeypatch):
    from app.shared.config import settings
    from app.worker import tick
    monkeypatch.setattr(settings(), "storage_root", str(tmp_path / "private"))
    create_members(client)
    alice, bob, admin = tab_login(client, "alice", "member-password"), tab_login(client, "bob", "member-password"), tab_login(client)
    owner = headers(alice)
    response = client.post("/api/v1/notes", headers=owner, json={"title": "Alice private", "kind": "image"})
    assert response.status_code == 201
    note_id = response.json()["id"]
    data = png()
    response = client.post(f"/api/v1/notes/{note_id}/uploads", headers=owner, json={
        "filename": "private.png", "mime": "image/png", "size": len(data), "request_key": "tab-image-upload"})
    assert response.status_code == 201
    upload = response.json()
    assert client.put(upload["upload_url"], headers=owner | {"Content-Type": "image/png"}, content=data).status_code == 200
    assert client.post(f"/api/v1/uploads/{upload['id']}/complete", headers=owner, json={"revision": 1}).status_code == 200
    assert tick()
    file_id = upload["file"]["id"]
    response = client.post(f"/api/v1/files/{file_id}/ticket?kind=original", headers=owner, json={})
    assert response.status_code == 200
    url = response.json()["url"]
    assert client.get(url, headers=owner).content == data
    for other in (bob, admin):
        assert client.get(f"/api/v1/notes/{note_id}", headers=headers(other)).status_code == 404
        assert client.get(url, headers=headers(other)).status_code == 404
    assert client.post("/api/v1/auth/logout", headers=owner, json={}).status_code == 200
    assert client.get(url, headers=owner).status_code == 401
