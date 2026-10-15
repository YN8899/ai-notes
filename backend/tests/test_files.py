import io
from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from app.main import app
from app.modules.files import service
from app.modules.files.oss_storage import OSSStorage, StorageUnavailable
from app.modules.files.models import File, FileTicket, FileVariant, NotePage, UploadSession
from app.modules.jobs import service as jobs
from app.shared.config import settings
from app.shared.db import SessionLocal, utcnow
from app.shared.models import Job
from app.worker import tick
from conftest import invite, login, mutate, register


@pytest.fixture(autouse=True)
def private_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "storage_root", str(tmp_path / "private"))


def png():
    out = io.BytesIO()
    Image.new("RGB", (200, 300), "white").save(out, format="PNG")
    return out.getvalue()


def note(client):
    result = mutate(client, "POST", "/notes", {"title": "手写笔记", "kind": "image"})
    assert result.status_code == 201, result.text
    return result.json()


def upload(client, target, data=None, key="image-request-001"):
    data = png() if data is None else data
    result = mutate(client, "POST", f"/notes/{target['id']}/uploads", {
        "filename": "手写.png", "mime": "image/png", "size": len(data), "request_key": key})
    assert result.status_code == 201, result.text
    return result.json(), data


def put(client, item, data):
    csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    return client.put(item["upload_url"], content=data, headers={
        "Content-Type": "image/png", "Origin": "http://testserver", "X-CSRF-Token": csrf})


def complete(client, item, revision):
    return mutate(client, "POST", f"/uploads/{item['id']}/complete", {"revision": revision})


def test_upload_process_read_and_idempotency(client):
    login(client)
    target = note(client)
    item, data = upload(client, target)
    again, _ = upload(client, target)
    assert again["id"] == item["id"]
    assert complete(client, item, 1).status_code == 409
    assert put(client, item, data).status_code == 200
    assert put(client, item, data).status_code == 200
    finished = complete(client, item, 1)
    assert finished.status_code == 200, finished.text
    assert finished.json()["note_revision"] == 2
    assert complete(client, item, 1).json()["page"]["id"] == finished.json()["page"]["id"]
    assert put(client, item, data).status_code == 409
    assert tick()
    page = client.get(f"/api/v1/notes/{target['id']}/pages").json()[0]
    assert page["file"]["status"] == "ready"
    assert page["file"]["width"] == 200
    ticket = mutate(client, "POST", f"/files/{page['file']['id']}/ticket?kind=original", {})
    assert ticket.status_code == 200, ticket.text
    assert client.get(ticket.json()["url"]).content == data
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(NotePage)) == 1
        assert db.scalar(select(func.count()).select_from(Job)) == 1
        assert db.scalar(select(func.count()).select_from(FileVariant)) == 2
    versions = client.get(f"/api/v1/notes/{target['id']}/versions").json()
    assert versions[0]["snapshot"]["pages"][0]["file_id"] == page["file"]["id"]


def test_fake_size_type_invalid_image_and_decoding_limit(client, monkeypatch):
    login(client)
    target = note(client)
    item, data = upload(client, target)
    assert put(client, item, data + b"x").status_code == 422
    assert put(client, item, b"x" * len(data)).status_code == 200
    assert complete(client, item, 1).status_code == 200
    assert tick()
    page = client.get(f"/api/v1/notes/{target['id']}/pages").json()[0]
    assert page["file"]["status"] == "rejected"
    assert mutate(client, "POST", f"/files/{page['file']['id']}/ticket", {}).status_code == 409
    monkeypatch.setattr(settings(), "upload_max_pixels", 100)
    second, raw = upload(client, target, key="image-request-002")
    assert put(client, second, raw).status_code == 200
    assert complete(client, second, 2).status_code == 200
    assert tick()
    assert client.get(f"/api/v1/notes/{target['id']}/pages").json()[1]["file"]["error_code"] == "image_pixels_exceeded"


def test_file_privacy_admin_and_deleted_note(client):
    code = invite(client, 2)["code"]
    register(client, code)
    target = note(client)
    item, data = upload(client, target)
    put(client, item, data)
    complete(client, item, 1)
    tick()
    file_id = item["file"]["id"]
    ticket = mutate(client, "POST", f"/files/{file_id}/ticket", {}).json()["url"]
    with TestClient(app) as other:
        register(other, code, "bob")
        assert other.get(f"/api/v1/notes/{target['id']}/pages").status_code == 404
        assert other.get(ticket).status_code == 404
        assert mutate(other, "POST", f"/files/{file_id}/ticket", {}).status_code == 404
        assert put(other, item, data).status_code == 404
    with TestClient(app) as admin:
        login(admin)
        assert admin.get(ticket).status_code == 404
        assert admin.get(f"/api/v1/uploads/{item['id']}").status_code == 404
    assert mutate(client, "DELETE", f"/notes/{target['id']}", {"revision": 2}).status_code == 204
    assert client.get(ticket).status_code == 404
    assert mutate(client, "POST", f"/notes/{target['id']}/restore", {"revision": 3}).status_code == 200
    assert client.get(ticket).status_code == 200


def test_expired_upload_and_download_ticket(client):
    login(client)
    target = note(client)
    item, data = upload(client, target)
    put(client, item, data)
    with SessionLocal.begin() as db:
        db.get(UploadSession, UUID(item["id"])).expires_at = utcnow() - timedelta(seconds=1)
    assert complete(client, item, 1).status_code == 410
    service.maintenance()
    with SessionLocal() as db:
        assert db.get(File, UUID(item["file"]["id"])).status == "expired"
    second, data = upload(client, target, key="image-request-002")
    put(client, second, data)
    complete(client, second, 1)
    tick()
    ticket = mutate(client, "POST", f"/files/{second['file']['id']}/ticket", {}).json()["url"]
    with SessionLocal.begin() as db:
        db.scalar(select(FileTicket)).expires_at = utcnow() - timedelta(seconds=1)
    assert client.get(ticket).status_code == 403


def test_capacity_reservation_and_limits(client, monkeypatch):
    login(client)
    target = note(client)
    monkeypatch.setattr(settings(), "storage_quota_bytes", len(png()))
    upload(client, target)
    payload = {"filename": "x.png", "mime": "image/png", "size": len(png()), "request_key": "second-request"}
    assert mutate(client, "POST", f"/notes/{target['id']}/uploads", payload).status_code == 413
    monkeypatch.setattr(settings(), "storage_quota_bytes", 100_000_000)
    monkeypatch.setattr(settings(), "upload_batch_limit", 1)
    assert mutate(client, "POST", f"/notes/{target['id']}/uploads", payload).status_code == 429
    monkeypatch.setattr(settings(), "upload_max_bytes", 1)
    assert mutate(client, "POST", f"/notes/{target['id']}/uploads", payload).status_code == 413


def test_page_reorder_conflict_and_foreign_page(client):
    login(client)
    target = note(client)
    first, data = upload(client, target)
    put(client, first, data)
    a = complete(client, first, 1).json()["page"]
    second, data = upload(client, target, key="image-request-002")
    put(client, second, data)
    b = complete(client, second, 2).json()["page"]
    path = f"/notes/{target['id']}/pages/order"
    assert mutate(client, "PATCH", path, {"revision": 2, "page_ids": [b["id"], a["id"]]}).status_code == 409
    assert mutate(client, "PATCH", path, {"revision": 3, "page_ids": [a["id"], a["id"]]}).status_code == 422
    response = mutate(client, "PATCH", path, {"revision": 3, "page_ids": [b["id"], a["id"]]})
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 4
    assert [p["id"] for p in client.get(f"/api/v1/notes/{target['id']}/pages").json()] == [b["id"], a["id"]]


def test_expired_worker_does_not_publish_variants(client):
    login(client)
    target = note(client)
    item, data = upload(client, target)
    put(client, item, data)
    complete(client, item, 1)
    old = jobs.claim()
    with SessionLocal.begin() as db:
        db.get(Job, old["id"]).lease_until = utcnow() - timedelta(seconds=1)
    new = jobs.claim()
    assert not service.process_job(old)
    assert service.process_job(new)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(FileVariant)) == 2
        assert db.get(File, UUID(item["file"]["id"])).status == "ready"


def test_database_rejects_foreign_page_file(client):
    from sqlalchemy.exc import IntegrityError

    code = invite(client, 2)["code"]
    alice = register(client, code).json()
    a = note(client)
    with TestClient(app) as other:
        register(other, code, "bob")
        b = note(other)
        item, _ = upload(other, b)
    with SessionLocal() as db:
        db.add(NotePage(owner_id=UUID(alice["id"]), note_id=UUID(a["id"]),
                        file_id=UUID(item["file"]["id"]), position=0, revision=1))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


def test_oss_proxy_returns_image_bytes_without_oss_url_or_headers(client, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from test_oss_storage import Body

    objects = {}
    sdk = Mock()

    def store(request):
        objects[request.key] = request.body
        return SimpleNamespace()

    sdk.put_object.side_effect = store
    sdk.head_object.side_effect = lambda request: SimpleNamespace(content_length=len(objects[request.key]))
    sdk.get_object.side_effect = lambda request: SimpleNamespace(
        content_length=len(objects[request.key]), body=Body([objects[request.key]]),
        content_disposition="attachment", headers={"x-oss-force-download": "true"})
    cfg = settings()
    adapter = OSSStorage(cfg, client=sdk)
    monkeypatch.setattr(cfg, "storage_backend", "oss")
    monkeypatch.setattr(service, "_oss_storage", lambda: adapter)
    login(client)
    target = note(client)
    item, data = upload(client, target)
    assert put(client, item, data).status_code == 200
    assert complete(client, item, 1).status_code == 200
    assert tick()
    ticket = mutate(client, "POST", f"/files/{item['file']['id']}/ticket?kind=original", {})
    url = ticket.json()["url"]
    assert url.startswith("/api/v1/files/") and "aliyuncs.com" not in url
    response = client.get(url)
    assert response.status_code == 200 and response.content == data
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"] == "inline"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "x-oss-force-download" not in response.headers
    assert sdk.get_object.call_args.args[0].key.startswith("ai-notes/")


def test_proxy_storage_failure_is_safe_and_retry_is_owner_only(client, monkeypatch):
    invitation = invite(client, 2)["code"]
    register(client, invitation)
    target = note(client)
    item, raw = upload(client, target)
    put(client, item, raw)
    complete(client, item, 1)
    job = jobs.claim()
    # Exhausted transient image processing is visible to the owner and recoverable.
    with SessionLocal.begin() as db:
        row = db.get(Job, job["id"])
        row.status, row.worker_token, row.lease_until = "failed", None, None
        row.error_code = "handler_failed"
    page = client.get(f"/api/v1/notes/{target['id']}/pages").json()[0]
    assert page["file"]["status"] == "failed"
    with TestClient(app) as other:
        register(other, invitation, "bob")
        assert mutate(other, "POST", f"/files/{item['file']['id']}/retry", {}).status_code == 404
    result = mutate(client, "POST", f"/files/{item['file']['id']}/retry", {})
    assert result.status_code == 200 and result.json()["status"] == "processing"
    assert tick()
    ticket = mutate(client, "POST", f"/files/{item['file']['id']}/ticket", {}).json()["url"]

    def fail(*_):
        raise StorageUnavailable()

    monkeypatch.setattr(service.LocalStorage, "read", fail)
    response = client.get(ticket)
    assert response.status_code == 503 and response.json()["code"] == "file_unavailable"
    assert "traceback" not in response.text.lower()
