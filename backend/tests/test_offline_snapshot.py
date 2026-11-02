import hashlib
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.modules.files import service as files
from app.modules.files.models import File, FileVariant, NotePage
from app.modules.notes.models import Note
from app.modules.offline import assets, snapshot
from app.modules.offline.models import OfflineSyncState
from app.shared.config import settings
from app.shared.db import SessionLocal, engine, utcnow
from app.shared.models import AuthSession, User
from app.worker import tick
from conftest import invite, login, mutate, register
from test_offline_sync import image_page, new_note, operation, pdf_bytes, png, sync, upload_bytes


@pytest.fixture(autouse=True)
def private_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "storage_root", str(tmp_path / "private"))


def get_snapshot(client, scope="all", note_ids=None, status=200):
    response = mutate(client, "POST", "/offline/snapshot", {"scope": scope, "note_ids": note_ids or []})
    assert response.status_code == status, response.text
    return response.json()


def test_snapshot_complete_owner_organization_text_trash_images_pdf_and_assets(client):
    login(client)
    folder = sync(client, operation("folder.create", payload={"name": "全部组织"}))["resource_id"]
    book = sync(client, operation("notebook.create", payload={"name": "一本", "folder_id": folder}))["resource_id"]
    tag = sync(client, operation("tag.create", payload={"name": "一个"}))["resource_id"]
    text = new_note(client, notebook_id=book, tag_ids=[tag], plain_text="完整回收站正文")
    sync(client, operation("note.trash", text, revision=1))
    for format, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg")):
        target = new_note(client, "image")
        image_page(client, target, data=png(format), mime=mime)
    raw = pdf_bytes()
    pdf = sync(client, operation("pdf.create", payload={"filename": "完整.pdf", "size": len(raw)}))
    upload_bytes(client, pdf["transport"]["upload_id"], raw, "application/pdf")
    sync(client, operation("pdf.start", pdf["transport"]["import_id"], revision=1))
    assert tick() and tick() and tick()
    result = get_snapshot(client)
    assert len(result["folders"]) == len(result["notebooks"]) == len(result["tags"]) == 1
    assert len(result["notes"]) == 4
    trash = next(note for note in result["notes"] if note["id"] == text)
    assert trash["deleted_at"] and trash["plain_text"] == "完整回收站正文" and trash["tag_ids"] == [tag]
    assert len(result["pages"]) == 4 and result["pdf_imports"][0]["completed_pages"] == 2
    assert {file["mime"] for file in result["files"]} == {"image/png", "image/jpeg", "application/pdf"}
    assert len(result["assets"]) == 13 and all(asset["available"] for asset in result["assets"])
    for asset in result["assets"]:
        response = client.get(f"/api/v1/offline/files/{asset['file_id']}/content?kind={asset['kind']}")
        assert response.status_code == 200, response.text
        assert response.headers["x-content-sha256"] == hashlib.sha256(response.content).hexdigest()
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-type"].startswith(asset["mime"])
    serialized = str(result)
    assert "object_key" not in serialized and "ticket" not in serialized and "upload_url" not in serialized
    assert get_snapshot(client)["content_version"] == result["content_version"]
    assert get_snapshot(client)["snapshot_id"] != result["snapshot_id"]


def test_selected_snapshot_keeps_full_organization_and_rejects_foreign_id(client):
    code = invite(client, 2)["code"]
    register(client, code, "alice")
    folder = sync(client, operation("folder.create", payload={"name": "空组织也保留"}))["resource_id"]
    own = new_note(client, plain_text="own")
    omitted = new_note(client, plain_text="omitted")
    with TestClient(app) as bob:
        register(bob, code, "bob")
        foreign = new_note(bob, plain_text="foreign-secret")
    result = get_snapshot(client, "selected", [own])
    assert [note["id"] for note in result["notes"]] == [own]
    assert result["folders"][0]["id"] == folder
    assert "foreign-secret" not in str(result) and omitted not in str(result)
    assert get_snapshot(client, "selected", [own, foreign], 404)["code"] == "not_found"
    assert get_snapshot(client, "selected", []) ["notes"] == []


def test_snapshot_generation_stable_owner_scoped_and_nonportable(client):
    code = invite(client, 2)["code"]
    register(client, code, "alice")
    first = get_snapshot(client)
    assert get_snapshot(client)["sync_epoch"] == first["sync_epoch"]
    with TestClient(app) as other:
        register(other, code, "bob")
        second = get_snapshot(other)
        assert second["owner_id"] != first["owner_id"]
        assert second["sync_epoch"] != first["sync_epoch"]
        assert get_snapshot(other)["sync_epoch"] == second["sync_epoch"]
    with SessionLocal() as db:
        assert db.get(OfflineSyncState, UUID(first["owner_id"])).epoch == UUID(first["sync_epoch"])
        assert db.get(OfflineSyncState, UUID(second["owner_id"])).epoch == UUID(second["sync_epoch"])


def test_snapshot_has_one_rr_view_across_multiple_notes_and_concurrent_commit(client):
    login(client)
    first = new_note(client, plain_text="before-first")
    second = new_note(client, plain_text="before-second")
    changed = []

    def concurrent(connection, cursor, statement, parameters, context, executemany):
        if "FROM folders" in statement and not changed:
            changed.append(True)
            with SessionLocal.begin() as db:
                for note_id in (first, second):
                    row = db.get(Note, UUID(note_id))
                    row.plain_text = "after-concurrent-commit"
                    row.revision += 1

    event.listen(engine, "after_cursor_execute", concurrent)
    try:
        result = get_snapshot(client)
    finally:
        event.remove(engine, "after_cursor_execute", concurrent)
    assert changed and {note["plain_text"] for note in result["notes"]} == {"before-first", "before-second"}
    assert {note["plain_text"] for note in get_snapshot(client)["notes"]} == {"after-concurrent-commit"}


@pytest.mark.parametrize("limit", ["export_max_rows", "export_max_metadata_bytes", "export_max_bytes"])
def test_snapshot_limits_reject_entire_response_without_truncation(client, monkeypatch, limit):
    login(client)
    new_note(client, plain_text="x" * 500)
    new_note(client, plain_text="y" * 500)
    monkeypatch.setattr(settings(), limit, 1)
    result = get_snapshot(client, status=413)
    assert result["code"] == "offline_snapshot_too_large" and "notes" not in result


def test_snapshot_pool_one_and_end_auth_revalidation(client, monkeypatch):
    login(client)
    new_note(client)
    single = create_engine(engine.url, pool_size=1, max_overflow=0, pool_timeout=0.25)
    monkeypatch.setattr(snapshot, "SessionLocal", sessionmaker(bind=single, expire_on_commit=False))
    try:
        assert len(get_snapshot(client)["notes"]) == 1
    finally:
        single.dispose()
    original = snapshot.create

    def revoke(owner_id, data):
        result = original(owner_id, data)
        with SessionLocal.begin() as db:
            for session in db.scalars(select(AuthSession).where(AuthSession.user_id == owner_id)):
                session.revoked_at = utcnow()
        return result

    monkeypatch.setattr(snapshot, "create", revoke)
    assert get_snapshot(client, status=401)["code"] == "login_required"


def test_asset_trash_owner_boundaries_unlink_and_immutable_original(client):
    code = invite(client, 2)["code"]
    register(client, code, "alice")
    note_id = new_note(client, "image")
    created, _, completion, original = image_page(client, note_id)
    assert tick()
    file_id = created["transport"]["file_id"]
    path = f"/api/v1/offline/files/{file_id}/content?kind=original"
    sync(client, operation("note.trash", note_id, revision=2))
    assert client.get(path).content == original
    with TestClient(app) as other:
        register(other, code, "bob")
        assert other.get(path).status_code == 404
        login(other)
        assert other.get(path).status_code == 404
    with TestClient(app) as anonymous:
        assert anonymous.get(path).status_code == 401
    sync(client, operation("note.restore", note_id, revision=3))
    sync(client, operation("page.remove", completion["transport"]["page_id"], {"note_id": note_id}, 4))
    assert client.get(path).status_code == 404
    with SessionLocal() as db:
        assert db.get(File, UUID(file_id)) is not None


@pytest.mark.parametrize("damage", ["missing", "same_length_corrupt", "bad_variant"])
def test_asset_missing_and_corrupt_bytes_fail_without_storage_keys(client, damage):
    login(client)
    note_id = new_note(client, "image")
    created = image_page(client, note_id)[0]
    assert tick()
    file_id = created["transport"]["file_id"]
    kind = "original"
    with SessionLocal() as db:
        file = db.get(File, UUID(file_id))
        key = file.object_key
        size = file.bytes
        if damage == "bad_variant":
            variant = db.scalar(select(FileVariant).where(FileVariant.file_id == file.id, FileVariant.kind == "reading"))
            key, size, kind = variant.object_key, variant.bytes, "reading"
    if damage == "missing":
        files.storage().delete(key)
    else:
        files.storage().put(key, b"x" * size)
    response = client.get(f"/api/v1/offline/files/{file_id}/content?kind={kind}")
    assert response.status_code in {409, 422}
    assert key not in response.text and "object_key" not in response.text


def test_asset_end_revalidation_prevents_revoked_download(client, monkeypatch):
    login(client)
    note_id = new_note(client, "image")
    file_id = image_page(client, note_id)[0]["transport"]["file_id"]
    assert tick()
    real = assets.read

    def revoke(db, user, file_id, kind):
        data = real(db, user, file_id, kind)
        with SessionLocal.begin() as independent:
            for session in independent.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
                session.revoked_at = utcnow()
        return data

    monkeypatch.setattr(assets, "read", revoke)
    response = client.get(f"/api/v1/offline/files/{file_id}/content")
    assert response.status_code == 401 and response.headers["cache-control"] == "no-store"
