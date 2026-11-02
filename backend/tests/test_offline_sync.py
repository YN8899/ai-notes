"""Offline API integration against the exclusive disposable notes_test database."""
import io
import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete, event, func, select
from sqlalchemy.orm import Session

from app.main import app
from app.modules.files import service as files
from app.modules.files.models import File, NotePage, PdfImport, UploadSession
from app.modules.notes.models import Folder, Note, Notebook, NoteVersion, Tag
from app.modules.offline import service
from app.modules.offline.models import OfflineSyncReceipt, OfflineSyncState
from app.modules.offline.validation import validate_upload
from app.shared.config import settings
from app.shared.db import SessionLocal, utcnow
from app.shared.errors import AppError
from app.shared.models import AuthSession, Job, User
from app.worker import tick
from conftest import invite, login, mutate, register


@pytest.fixture(autouse=True)
def private_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "storage_root", str(tmp_path / "private"))


def operation(kind, resource_id=None, payload=None, revision=None, operation_id=None, sync_epoch=None):
    data = {"operation_id": str(operation_id or uuid4()), "kind": kind,
            "resource_id": str(resource_id or uuid4()), "base_revision": revision,
            "payload": {} if payload is None else payload}
    if sync_epoch is not None:
        data["sync_epoch"] = str(sync_epoch)
    return data


def epoch(client):
    response = mutate(client, "POST", "/offline/snapshot", {"scope": "selected", "note_ids": []})
    assert response.status_code == 200, response.text
    return response.json()["sync_epoch"]


def sync(client, data, status=200):
    # Freeze the generation before its first transport. Replays retain the old
    # value; a fresh snapshot must never silently rewrite a previously sent op.
    if "sync_epoch" not in data:
        data["sync_epoch"] = epoch(client)
    response = mutate(client, "POST", "/offline/sync", data)
    assert response.status_code == status, response.text
    return response.json()


def new_note(client, kind="text", **extra):
    op = operation("note.create", payload={"title": "离线笔记", "kind": kind, **extra})
    result = sync(client, op)
    assert result["resource_id"] == op["resource_id"]
    return result["resource_id"]


def png(format="PNG", size=(30, 40)):
    output = io.BytesIO()
    Image.new("RGB", size, "green").save(output, format=format)
    return output.getvalue()


def pdf_bytes():
    output = io.BytesIO()
    first = Image.new("RGB", (50, 60), "red")
    second = Image.new("RGB", (50, 60), "blue")
    first.save(output, format="PDF", save_all=True, append_images=[second])
    return output.getvalue()


def upload_bytes(client, upload_id, data, mime="image/png"):
    csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    response = client.put(f"/api/v1/uploads/{upload_id}/content", content=data, headers={
        "Origin": "http://testserver", "X-CSRF-Token": csrf, "Content-Type": mime})
    assert response.status_code == 200, response.text


def image_page(client, note_id, revision=1, data=None, mime="image/png"):
    raw = png() if data is None else data
    created = sync(client, operation("upload.create", payload={
        "note_id": note_id, "filename": "原图.png", "mime": mime, "size": len(raw)}))
    upload_id = created["transport"]["upload_id"]
    upload_bytes(client, upload_id, raw, mime)
    completed = operation("upload.complete", upload_id, revision=revision)
    result = sync(client, completed)
    return created, completed, result, raw


@pytest.mark.parametrize("kind,model", [("folder", Folder), ("notebook", Notebook), ("tag", Tag)])
def test_organization_all_kinds_and_replay_after_delete(client, kind, model):
    login(client)
    create = operation(f"{kind}.create", payload={"name": "初始"})
    first = sync(client, create)
    assert sync(client, create) == first
    resource = create["resource_id"]
    update = operation(f"{kind}.update", resource, {"name": "修改"}, 1)
    assert sync(client, update)["applied_revision"] == 2
    assert sync(client, update)["applied_revision"] == 2
    conflict = sync(client, operation(f"{kind}.update", resource, {"name": "过期"}, 1), 409)
    assert conflict["current"][kind]["name"] == "修改"
    deletion = operation(f"{kind}.delete", resource, revision=2)
    deleted = sync(client, deletion)
    assert sync(client, deletion) == deleted
    # Success facts must be served before current-resource validation.
    assert sync(client, create) == first
    with SessionLocal() as db:
        assert db.get(model, UUID(resource)) is None
        assert db.scalar(select(func.count()).select_from(OfflineSyncReceipt)) == 3


def test_organization_dependencies_cycle_nonempty_and_relation_owner(client):
    login(client)
    parent = sync(client, operation("folder.create", payload={"name": "父"}))["resource_id"]
    child = sync(client, operation("folder.create", payload={"name": "子", "parent_id": parent}))["resource_id"]
    sync(client, operation("folder.update", parent, {"parent_id": child}, 1), 409)
    sync(client, operation("folder.delete", parent, revision=1), 409)
    notebook = sync(client, operation("notebook.create", payload={"name": "笔记本", "folder_id": child}))["resource_id"]
    tag = sync(client, operation("tag.create", payload={"name": "标记"}))["resource_id"]
    note_id = new_note(client, notebook_id=notebook, tag_ids=[tag])
    sync(client, operation("notebook.delete", notebook, revision=1), 409)
    sync(client, operation("tag.delete", tag, revision=1), 409)
    sync(client, operation("note.update", note_id, {"notebook_id": None, "tag_ids": []}, 1))
    sync(client, operation("notebook.delete", notebook, revision=1))
    sync(client, operation("tag.delete", tag, revision=1))


def test_note_all_kinds_full_conflict_and_lost_response_replay(client):
    login(client)
    body = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "原文"}]}]}
    note_id = new_note(client, body_json=body, plain_text="原文")
    update = operation("note.update", note_id, {"title": "另设备标题", "plain_text": "原文"}, 1)
    acknowledged = sync(client, update)
    # Treat the first committed response as lost; retry original immutable operation.
    assert sync(client, update) == acknowledged
    stale = sync(client, operation("note.update", note_id, {"title": "本机标题"}, 1), 409)
    assert stale["current"]["note"]["body_json"] == body
    assert stale["current"]["note"]["title"] == "另设备标题"
    trash = operation("note.trash", note_id, revision=2)
    sync(client, trash)
    deleted_conflict = sync(client, operation("note.update", note_id, {"title": "不得恢复"}, 3), 409)
    assert deleted_conflict["code"] == "note_deleted"
    assert deleted_conflict["current"]["note"]["deleted_at"]
    restored = sync(client, operation("note.restore", note_id, revision=3))
    assert restored["applied_revision"] == 4
    assert sync(client, update) == acknowledged
    with SessionLocal() as db:
        assert db.get(Note, UUID(note_id)).revision == 4
        assert db.scalar(select(func.count()).select_from(NoteVersion).where(NoteVersion.note_id == UUID(note_id))) == 4


def test_same_operation_different_payload_rejected_without_content_or_url_receipt(client):
    login(client)
    note_id = new_note(client)
    update = operation("note.update", note_id, {"plain_text": "private-body-never-in-receipt"}, 1)
    first = sync(client, update)
    changed = {**update, "payload": {"plain_text": "changed"}}
    assert sync(client, changed, 409)["code"] == "operation_conflict"
    assert sync(client, update) == first
    with SessionLocal() as db:
        row = db.scalar(select(OfflineSyncReceipt).where(OfflineSyncReceipt.operation_id == UUID(update["operation_id"])))
        stored = json.dumps({key: getattr(row, key) for key in ("resource_mapping", "transport")})
        assert "private-body" not in stored and "http" not in stored and "token" not in stored
        assert db.get(Note, UUID(note_id)).plain_text == "private-body-never-in-receipt"


def test_receipt_insert_failure_rolls_back_business_and_version(client):
    login(client)
    note_id = new_note(client)
    update = operation("note.update", note_id, {"title": "must-rollback"}, 1)

    def fail_receipt(mapper, connection, target):
        raise RuntimeError("synthetic receipt insert failure")

    event.listen(OfflineSyncReceipt, "before_insert", fail_receipt)
    try:
        sync(client, update, 500)
    finally:
        event.remove(OfflineSyncReceipt, "before_insert", fail_receipt)
    with SessionLocal() as db:
        assert db.get(Note, UUID(note_id)).revision == 1
        assert db.get(Note, UUID(note_id)).title == "离线笔记"
        assert db.scalar(select(func.count()).select_from(NoteVersion)) == 1
        assert db.scalar(select(func.count()).select_from(OfflineSyncReceipt)) == 1
    assert sync(client, update)["applied_revision"] == 2


def test_upload_complete_is_validated_before_reference_and_replayed_once(client):
    login(client)
    note_id = new_note(client, "image")
    created, completion, result, raw = image_page(client, note_id)
    assert sync(client, completion) == result
    upload_id = created["transport"]["upload_id"]
    status = client.get(f"/api/v1/uploads/{upload_id}")
    assert status.status_code == 200
    assert status.json()["upload_url"].endswith(f"{upload_id}/content")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(NotePage)) == 1
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "image.process")) == 1
        assert db.get(File, UUID(created["transport"]["file_id"])).sha256
        row = db.scalar(select(OfflineSyncReceipt).where(OfflineSyncReceipt.operation_id == UUID(completion["operation_id"])))
        assert "url" not in json.dumps(row.transport)


@pytest.mark.parametrize("bad", ["corrupt", "mime", "animated", "pixels"])
def test_image_invalid_never_creates_page_job_or_receipt(client, monkeypatch, bad):
    login(client)
    note_id = new_note(client, "image")
    data = png()
    if bad == "corrupt":
        data = b"not an image"
    elif bad == "mime":
        data = png("JPEG")
    elif bad == "animated":
        stream = io.BytesIO()
        Image.new("RGBA", (20, 20), "red").save(stream, format="PNG", save_all=True,
            append_images=[Image.new("RGBA", (20, 20), "blue")], duration=100, loop=0)
        data = stream.getvalue()
    else:
        monkeypatch.setattr(settings(), "upload_max_pixels", 50)
    created = sync(client, operation("upload.create", payload={"note_id": note_id,
        "filename": "bad.png", "mime": "image/png", "size": len(data)}))
    upload_id = created["transport"]["upload_id"]
    upload_bytes(client, upload_id, data)
    rejected = operation("upload.complete", upload_id, revision=1)
    sync(client, rejected, 422)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(NotePage)) == 0
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        assert db.scalar(select(func.count()).select_from(OfflineSyncReceipt).where(
            OfflineSyncReceipt.operation_id == UUID(rejected["operation_id"]))) == 0
        assert db.get(Note, UUID(note_id)).revision == 1


def test_page_order_remove_preserve_file_and_history_and_detect_concurrent_append(client):
    login(client)
    note_id = new_note(client, "image")
    first = image_page(client, note_id, 1)[2]
    second = image_page(client, note_id, 2)[2]
    page1, page2 = first["transport"]["page_id"], second["transport"]["page_id"]
    stale = operation("page.order", note_id, {"page_ids": [page1]}, 2)
    state = sync(client, stale, 409)["current"]
    assert len(state["pages"]) == 2 and state["note"]["revision"] == 3
    order = operation("page.order", note_id, {"page_ids": [page2, page1]}, 3)
    assert sync(client, order)["applied_revision"] == 4
    remove = operation("page.remove", page1, {"note_id": note_id}, 4)
    assert sync(client, remove)["applied_revision"] == 5
    assert sync(client, order)["applied_revision"] == 4
    assert sync(client, remove)["applied_revision"] == 5
    with SessionLocal() as db:
        assert db.get(NotePage, UUID(page1)).deleted_at is not None
        assert db.get(File, UUID(first["transport"]["file_id"])) is not None
        assert db.scalar(select(func.count()).select_from(NoteVersion)) == 5
        history = db.scalar(select(NoteVersion).where(NoteVersion.note_id == UUID(note_id), NoteVersion.revision == 4))
        assert page1 in {page["id"] for page in history.snapshot["pages"]}


@pytest.mark.parametrize("endpoint", ["offline", "legacy"])
def test_completed_upload_cannot_reattach_removed_page_but_original_receipt_replays(client, endpoint):
    login(client)
    note_id = new_note(client, "image")
    created, original_operation, original_result, _ = image_page(client, note_id)
    upload_id = created["transport"]["upload_id"]
    page_id = original_result["transport"]["page_id"]
    sync(client, operation("page.remove", page_id, {"note_id": note_id}, 2))
    # The committed operation is still a success fact; it does not execute the
    # upload service or resurrect the now deliberately removed current page.
    assert sync(client, original_operation) == original_result
    new_operation = operation("upload.complete", upload_id, revision=3)
    if endpoint == "offline":
        result = sync(client, new_operation, 409)
        assert result["current"]["note"]["revision"] == 3
        assert result["current"]["pages"] == []
    else:
        response = mutate(client, "POST", f"/uploads/{upload_id}/complete", {"revision": 3})
        assert response.status_code == 409, response.text
        result = response.json()
    assert result["code"] == "upload_target_removed"
    assert sync(client, original_operation) == original_result
    with SessionLocal() as db:
        assert db.get(NotePage, UUID(page_id)).deleted_at is not None
        assert db.get(File, UUID(created["transport"]["file_id"])) is not None
        assert db.get(UploadSession, UUID(upload_id)).status == "completed"
        assert db.get(Note, UUID(note_id)).revision == 3
        assert db.scalar(select(func.count()).select_from(NoteVersion)) == 3
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "image.process")) == 1
        assert db.scalar(select(func.count()).select_from(OfflineSyncReceipt)) == 4
        assert db.scalar(select(func.count()).select_from(OfflineSyncReceipt).where(
            OfflineSyncReceipt.operation_id == UUID(new_operation["operation_id"]))) == 0


def test_pdf_create_mapping_start_prevalidation_and_pending_worker(client):
    login(client)
    raw = pdf_bytes()
    create = operation("pdf.create", payload={"filename": "笔记.pdf", "size": len(raw)})
    item = sync(client, create)
    assert sync(client, create) == item
    note_id = item["transport"]["note_id"]
    assert item["resource_id"] == note_id
    assert item["resource_mapping"] == {create["resource_id"]: note_id}
    upload_bytes(client, item["transport"]["upload_id"], raw, "application/pdf")
    start = operation("pdf.start", item["transport"]["import_id"], revision=1)
    started = sync(client, start)
    assert sync(client, start) == started
    sync(client, operation("page.order", note_id, {"page_ids": [str(uuid4())]}, 1), 409)
    with SessionLocal() as db:
        imported = db.get(PdfImport, UUID(item["transport"]["import_id"]))
        assert imported.status == "queued" and imported.page_count == 2 and imported.completed_pages == 0
        assert db.scalar(select(func.count()).select_from(NotePage)) == 0
        assert db.scalar(select(func.count()).select_from(Note)) == 1
    assert tick()
    with SessionLocal() as db:
        assert db.get(PdfImport, UUID(item["transport"]["import_id"])).status == "ready"
        assert db.scalar(select(func.count()).select_from(NotePage)) == 2
    # Current note revision changed during rendering; old success remains valid.
    assert sync(client, start) == started


@pytest.mark.parametrize("bad", ["corrupt", "encrypted", "pages"])
def test_pdf_rejects_before_queue(client, monkeypatch, bad):
    login(client)
    raw = (Path(__file__).parent / "fixtures" / "password-required.pdf").read_bytes() if bad == "encrypted" else pdf_bytes()
    if bad == "corrupt":
        raw = b"%PDF-1.7\nnot-a-valid-document"
    if bad == "pages":
        monkeypatch.setattr(settings(), "pdf_max_pages", 1)
    item = sync(client, operation("pdf.create", payload={"filename": "bad.pdf", "size": len(raw)}))
    upload_bytes(client, item["transport"]["upload_id"], raw, "application/pdf")
    start = operation("pdf.start", item["transport"]["import_id"], revision=1)
    sync(client, start, 422)
    with SessionLocal() as db:
        assert db.get(PdfImport, UUID(item["transport"]["import_id"])).status == "uploading"
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        assert db.scalar(select(func.count()).select_from(OfflineSyncReceipt)) == 1


def test_owner_admin_anonymous_uuid_collision_and_foreign_operation(client):
    code = invite(client, 2)["code"]
    register(client, code, "alice")
    note_id = new_note(client)
    update = operation("note.update", note_id, {"title": "alice private"}, 1)
    sync(client, update)
    with TestClient(app) as other:
        register(other, code, "bob")
        sync(other, operation("note.update", note_id, {"title": "steal"}, 2), 404)
        collision = sync(other, operation("note.create", note_id, {"title": "collision"}), 409)
        assert collision["code"] == "resource_conflict" and collision["current"] is None
        sync(other, update, 409)  # Alice's generation is not Bob's generation.
        sync(other, {**update, "sync_epoch": epoch(other)}, 404)
    with TestClient(app) as admin:
        login(admin)
        sync(admin, operation("note.update", note_id, {"title": "admin steal"}, 2), 404)
    with TestClient(app) as anonymous:
        sync(anonymous, update, 401)
    with SessionLocal() as db:
        assert db.get(Note, UUID(note_id)).title == "alice private"
        assert db.scalar(select(func.count()).select_from(OfflineSyncReceipt)) == 2


def test_long_validation_rechecks_expiry_and_rolls_back(client, monkeypatch):
    login(client)
    note_id = new_note(client, "image")
    raw = png()
    item = sync(client, operation("upload.create", payload={"note_id": note_id,
        "filename": "image.png", "mime": "image/png", "size": len(raw)}))
    upload_id = item["transport"]["upload_id"]
    upload_bytes(client, upload_id, raw)
    real_apply = service.apply

    def expire(db, user, data):
        result = real_apply(db, user, data)
        for session in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
            session.expires_at = utcnow() - timedelta(seconds=1)
        db.flush()
        return result

    monkeypatch.setattr(service, "apply", expire)
    sync(client, operation("upload.complete", upload_id, revision=1), 401)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(NotePage)) == 0
        assert db.scalar(select(func.count()).select_from(OfflineSyncReceipt)) == 2


def test_parallel_writers_one_revision_and_parallel_retry_one_receipt(client):
    login(client)
    note_id = new_note(client)
    with TestClient(app) as first, TestClient(app) as second:
        login(first)
        login(second)
        current_epoch = epoch(first)
        assert epoch(second) == current_epoch
        candidates = [operation("note.update", note_id, {"title": "parallel-a"}, 1, sync_epoch=current_epoch),
                      operation("note.update", note_id, {"title": "parallel-b"}, 1, sync_epoch=current_epoch)]
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(mutate, target, "POST", "/offline/sync", data)
                       for target, data in zip((first, second), candidates)]
            results = [future.result(timeout=20) for future in futures]
        assert sorted(result.status_code for result in results) == [200, 409]
        duplicate = operation("note.update", note_id, {"title": "same-stable-operation"}, 2, sync_epoch=current_epoch)
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(mutate, target, "POST", "/offline/sync", duplicate)
                       for target in (first, second)]
            repeated = [future.result(timeout=20) for future in futures]
        assert [response.status_code for response in repeated] == [200, 200]
        assert repeated[0].json() == repeated[1].json()
    with SessionLocal() as db:
        assert db.get(Note, UUID(note_id)).revision == 3
        assert db.scalar(select(func.count()).select_from(NoteVersion)) == 3
        assert db.scalar(select(func.count()).select_from(OfflineSyncReceipt)) == 3


def test_validator_deadline_terminates_and_joins_child():
    before = {child.pid for child in multiprocessing.active_children()}
    with pytest.raises(AppError) as caught:
        validate_upload("image", png(), mime="image/png", max_pixels=100_000, timeout=0.000001)
    assert caught.value.code == "validation_timeout"
    assert {child.pid for child in multiprocessing.active_children()} == before


def test_epoch_required_and_direct_sync_cannot_initialize_server_generation(client):
    login(client)
    missing = operation("note.create", payload={"title": "missing epoch"})
    response = mutate(client, "POST", "/offline/sync", missing)
    assert response.status_code == 422
    supplied = {**missing, "sync_epoch": str(uuid4())}
    response = mutate(client, "POST", "/offline/sync", supplied)
    assert response.status_code == 409
    assert response.json()["code"] == "offline_epoch_changed" and response.json()["current"] is None
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(OfflineSyncState)) == 0
        assert db.scalar(select(func.count()).select_from(Note)) == 0
        assert db.scalar(select(func.count()).select_from(OfflineSyncReceipt)) == 0


def test_wrong_epoch_rejects_before_receipts_and_all_business_mutation(client):
    login(client)
    good_epoch = epoch(client)
    successful = operation("note.create", payload={"title": "created once"}, sync_epoch=good_epoch)
    original = sync(client, successful)
    wrong = {**operation("note.update", original["resource_id"], {"title": "must not apply"}, 1),
             "sync_epoch": str(uuid4())}
    failure = sync(client, wrong, 409)
    assert failure["code"] == "offline_epoch_changed" and failure["current"] is None
    with SessionLocal.begin() as db:
        owner = db.scalar(select(User).where(User.username == "admin"))
        db.get(OfflineSyncState, owner.id).epoch = uuid4()
    # Existing success does not bypass a changed instance generation.
    assert sync(client, successful, 409)["code"] == "offline_epoch_changed"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(OfflineSyncReceipt)) == 1
        assert db.get(Note, UUID(original["resource_id"])).title == "created once"
        assert db.get(Note, UUID(original["resource_id"])).revision == 1


def test_account_restore_same_owner_uuid_new_epoch_blocks_lost_pdf_create_replay(client):
    login(client)
    old_epoch = epoch(client)
    raw = pdf_bytes()
    lost = operation("pdf.create", payload={"filename": "lost-response.pdf", "size": len(raw)}, sync_epoch=old_epoch)
    committed = sync(client, lost)
    real_note_id = committed["transport"]["note_id"]
    assert real_note_id != lost["resource_id"]
    with SessionLocal.begin() as db:
        owner = db.scalar(select(User).where(User.username == "admin"))
        same_owner_id = owner.id
        # Simulate account ZIP restoration: business IDs and owner UUID survive;
        # technical success facts/state are absent and upload keys are rebuilt.
        db.execute(delete(OfflineSyncReceipt).where(OfflineSyncReceipt.owner_id == owner.id))
        db.execute(delete(OfflineSyncState).where(OfflineSyncState.owner_id == owner.id))
        for upload in db.scalars(select(UploadSession).where(UploadSession.owner_id == owner.id)):
            upload.request_key = f"restored_{uuid4().hex}"
    login(client)  # Same owner can authenticate again; that cannot authorize replay.
    before_snapshot = sync(client, lost, 409)
    assert before_snapshot["code"] == "offline_epoch_changed" and before_snapshot["current"] is None
    with SessionLocal() as db:
        assert db.get(OfflineSyncState, same_owner_id) is None
    new_epoch = epoch(client)
    assert new_epoch != old_epoch
    rejected = sync(client, lost, 409)
    assert rejected["code"] == "offline_epoch_changed" and rejected["current"] is None
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Note)) == 1
        assert db.get(Note, UUID(real_note_id)).owner_id == same_owner_id
        assert db.scalar(select(func.count()).select_from(PdfImport)) == 1
        assert db.scalar(select(func.count()).select_from(OfflineSyncReceipt)) == 0
    # Fresh deliberately authored work uses a new operation and the fresh epoch.
    sync(client, operation("pdf.create", payload={"filename": "fresh.pdf", "size": len(raw)}, sync_epoch=new_epoch))
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Note)) == 2


@pytest.mark.parametrize("change", [
    {"kind": "note.execute"}, {"owner_id": str(uuid4())},
    {"payload": {"title": "x", "owner_id": str(uuid4())}},
    {"base_revision": True}, {"base_revision": 1.2},
    {"resource_id": "not-a-uuid"}, {"payload": {"title": "x", "unknown": 1}},
])
def test_unknown_kind_fields_and_non_strict_revisions_rejected(client, change):
    login(client)
    op = operation("note.create", payload={"title": "valid"})
    sync(client, {**op, **change}, 422)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Note)) == 0
        assert db.scalar(select(func.count()).select_from(OfflineSyncReceipt)) == 0
