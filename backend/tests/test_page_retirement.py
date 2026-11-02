from model_transport import install_model_transport
"""Real API lifecycle: removing current pages preserves retained source facts."""
import io
import json
import zipfile
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.modules.ai import chat_provider, chat_service
from app.modules.exports import service as exports
from app.modules.files import retention, service as files
from app.modules.files.models import File, NotePage, PdfImport
from app.modules.notes.models import Note, NoteVersion
from app.shared.config import settings
from app.shared.db import SessionLocal, engine, utcnow
from app.shared.models import AuthSession, User
from app.worker import tick
from conftest import login, mutate
from test_ai_chat import stream_bytes
from test_image_chat import submit as image_submit
from test_offline_sync import image_page, new_note, operation, pdf_bytes, sync, upload_bytes
from test_archive_restore import restore_tool, restore_database, target, _restore
from test_social import people, befriend, share
from test_community import published


@pytest.fixture(autouse=True)
def private_config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "storage_root", str(tmp_path / "objects"))
    monkeypatch.setattr(settings(), "deepseek_api_key", SecretStr("synthetic-page-retirement-test-key"))
    install_model_transport(monkeypatch, lambda request: httpx.Response(200, content=stream_bytes("保留的历史回答[[p:1]]")))


def ready_pdf(client):
    raw = pdf_bytes()
    imported = sync(client, operation("pdf.create", payload={"filename": "完整两页.pdf", "size": len(raw)}))
    upload_bytes(client, imported["transport"]["upload_id"], raw, "application/pdf")
    sync(client, operation("pdf.start", imported["transport"]["import_id"], revision=1))
    assert tick()
    note_id = imported["transport"]["note_id"]
    pages = client.get(f"/api/v1/notes/{note_id}/pages").json()
    assert len(pages) == 2
    return note_id, pages[0]["id"], 3, raw


def saved_image_answer(client):
    note_id = new_note(client, "image")
    _, _, completion, _ = image_page(client, note_id)
    assert tick()
    page_id = completion["transport"]["page_id"]
    response = image_submit(client, note_id, image={"note_id": note_id, "revision": 2, "page_id": page_id})
    assert response.status_code == 201, response.text
    run = response.json()
    assert chat_service.tick()
    saved = mutate(client, "POST", f"/ai/runs/{run['id']}/save-note", {"title": "保留来源的回答"})
    assert saved.status_code == 201, saved.text
    return note_id, page_id, 2, run, saved.json()


@pytest.mark.parametrize("kind,check", [("pdf", "export"), ("image", "export"), ("image", "retention")])
def test_page_removal_keeps_complete_export_and_retention(client, kind, check):
    login(client)
    if kind == "pdf":
        note_id, page_id, revision, _ = ready_pdf(client)
    else:
        note_id, page_id, revision, _, _ = saved_image_answer(client)
    sync(client, operation("page.remove", page_id, {"note_id": note_id}, revision))
    if check == "export":
        response = mutate(client, "POST", "/exports/archive", {})
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/zip")
    else:
        with engine.connect() as connection:
            assert retention.collect_references(connection)["keys"]


@pytest.mark.parametrize("kind", ["pdf", "image"])
def test_retired_pdf_and_saved_image_actual_export_verify_restore_roundtrip(
    client, kind, restore_tool, target, tmp_path, monkeypatch,
):
    login(client)
    run = None
    if kind == "pdf":
        note_id, page_id, revision, pdf = ready_pdf(client)
    else:
        note_id, page_id, revision, run, saved = saved_image_answer(client)
    removal = operation("page.remove", page_id, {"note_id": note_id}, revision)
    removed = sync(client, removal)
    assert sync(client, removal) == removed
    again = sync(client, operation("page.remove", page_id, {"note_id": note_id}, revision + 1), 409)
    assert again["code"] == "page_already_removed"
    current = client.get(f"/api/v1/notes/{note_id}/pages")
    assert current.status_code == 200 and len(current.json()) == (1 if kind == "pdf" else 0)
    snap = mutate(client, "POST", "/offline/snapshot", {"scope": "all", "note_ids": []}).json()
    assert page_id not in {page["id"] for page in snap["pages"]}
    with SessionLocal() as db:
        page = db.get(NotePage, UUID(page_id))
        assert page.deleted_at is not None and page.revision == 2
        owner_id, file_id = page.owner_id, page.file_id
        latest = db.scalar(select(NoteVersion).where(NoteVersion.note_id == UUID(note_id),
            NoteVersion.revision == revision + 1))
        assert page_id not in {item["id"] for item in latest.snapshot["pages"]}
        if kind == "pdf":
            item = db.scalar(select(PdfImport).where(PdfImport.note_id == UUID(note_id)))
            assert (item.status, item.page_count, item.completed_pages) == ("ready", 2, 2)
            source_file_id = item.source_file_id
    if kind == "pdf":
        raw = client.get(f"/api/v1/offline/files/{source_file_id}/content?kind=original")
        assert raw.status_code == 200 and raw.content == pdf
        import pypdfium2
        with pypdfium2.PdfDocument(raw.content) as document:
            assert len(document) == 2
        detail = client.get(f"/api/v1/ai/pdf-notebooks/{note_id}")
        assert detail.status_code == 200 and len(detail.json()["pages"]) == 1
        assert mutate(client, "POST", "/ai/pdf-preview", {"note_id": note_id,
            "revision": revision + 1, "mode": "all"}).status_code == 409
        assert mutate(client, "POST", "/ai/pdf-preview", {"note_id": note_id,
            "revision": revision + 1, "mode": "pages", "page_numbers": [1]}).status_code == 422
        assert mutate(client, "POST", "/ai/pdf-preview", {"note_id": note_id,
            "revision": revision + 1, "mode": "pages", "page_numbers": [2]}).status_code == 200
    else:
        historic = client.get(f"/api/v1/ai/runs/{run['id']}/source-pages/{page_id}")
        assert historic.status_code == 200 and historic.json()["source_changed"]
        ticket = mutate(client, "POST", f"/files/{file_id}/ticket", {})
        assert ticket.status_code == 200 and client.get(ticket.json()["url"]).status_code == 200
        assert mutate(client, "POST", "/ai/image-preview", {"note_id": note_id,
            "revision": revision + 1, "page_id": page_id}).status_code in {409, 422}
        assert client.get(f"/api/v1/notes/{saved['note_id']}/ai-answer").status_code == 200
    response = mutate(client, "POST", "/exports/archive", {})
    assert response.status_code == 200, response.text
    archive = tmp_path / f"retired-{kind}.zip"
    archive.write_bytes(response.content)
    verifier = restore_tool._load_verifier()
    assert verifier.verify_archive(archive)["note_pages"] == (2 if kind == "pdf" else 1)
    with zipfile.ZipFile(archive) as opened:
        manifest = json.loads(opened.read("manifest.json"))
        data = json.loads(opened.read("data.json"))["collections"]
        assert manifest["version"] == 5
        assert next(page for page in data["note_pages"] if page["id"] == page_id)["deleted_at"]
    _restore(restore_tool, archive, target)
    monkeypatch.setattr(settings(), "storage_root", str(target.storage_root))
    with target.sessions.begin() as db:
        user = db.get(User, owner_id)
        assert len(files.list_pages(db, user, UUID(note_id))) == (1 if kind == "pdf" else 0)
        assert db.get(NotePage, UUID(page_id)).deleted_at is not None
        session = AuthSession(user_id=owner_id, token_hash=uuid4().hex, csrf_hash="synthetic",
                              expires_at=utcnow() + timedelta(hours=1))
        db.add(session)
        db.flush()
        session_id = session.id
    with target.engine.connect() as connection:
        assert retention.collect_references(connection)["keys"]
    monkeypatch.setattr(exports, "SessionLocal", target.sessions)
    roundtrip = tmp_path / f"retired-{kind}-again.zip"
    with exports.create_archive(owner_id, session_id) as stream:
        roundtrip.write_bytes(stream.read())
    assert verifier.verify_archive(roundtrip)["note_pages"] == (2 if kind == "pdf" else 1)


def test_retired_page_keeps_ocr_learning_community_history_but_not_current_grants_or_search(
    client, people, monkeypatch, restore_tool, tmp_path,
):
    clients, ids = people
    owner, friend = clients["alice"], clients["bob"]
    monkeypatch.setattr(settings(), "deepseek_api_key", SecretStr("synthetic-page-retirement-test-key"))
    note_id = new_note(owner, "image")
    first = image_page(owner, note_id, 1)[2]
    second = image_page(owner, note_id, 2)[2]
    assert tick() and tick()
    page_id, second_id = first["transport"]["page_id"], second["transport"]["page_id"]
    ocr_body = {"request_key": str(uuid4()), "note_id": note_id, "page_id": page_id, "revision": 3}
    ocr = mutate(owner, "POST", "/ocr/documents", ocr_body)
    assert ocr.status_code == 201, ocr.text
    assert chat_service.tick()
    assert owner.get("/api/v1/search", params={"q": "保留的历史回答"}).json()["items"]
    deck = mutate(owner, "POST", "/learning/decks", {"name": "保留学习来源"})
    assert deck.status_code == 201, deck.text
    card = mutate(owner, "POST", "/learning/cards", {"deck_id": deck.json()["id"], "kind": "qa",
        "front": "历史问题", "back": "历史答案", "source": {"kind": "image", "note_id": note_id,
        "revision": 3, "page_id": page_id}})
    assert card.status_code == 201, card.text
    befriend(owner, friend, ids["bob"])
    grant = share(owner, owner.get(f"/api/v1/notes/{note_id}").json(), ids["bob"])
    shared = friend.get(f"/api/v1/social/shares/{grant['id']}")
    assert shared.status_code == 200, shared.text
    old_shared_url = next(page["image_url"] for page in shared.json()["pages"] if page["id"] == page_id)
    post = published(owner, source_note_id=note_id, source_revision=3, plain_text="")
    independent = friend.get(f"/api/v1/community/posts/{post['id']}").json()["assets"]
    assert len(independent) == 2
    retained_bytes = friend.get(independent[0]["original_url"]).content
    removed = operation("page.remove", page_id, {"note_id": note_id}, 3)
    assert sync(owner, removed)["applied_revision"] == 4
    assert sync(owner, operation("page.order", note_id, {"page_ids": [second_id]}, 4))["applied_revision"] == 5
    with SessionLocal() as db:
        retired, active = db.get(NotePage, UUID(page_id)), db.get(NotePage, UUID(second_id))
        assert retired.deleted_at and active.deleted_at is None
        assert retired.position == active.position == 0  # partial uniqueness only concerns current content
    assert owner.get("/api/v1/search", params={"q": "保留的历史回答"}).json()["items"] == []
    old_ocr = owner.get(f"/api/v1/ocr/documents/{ocr.json()['id']}")
    assert old_ocr.status_code == 200 and old_ocr.json()["source_valid"]
    corrected = mutate(owner, "PATCH", f"/ocr/documents/{ocr.json()['id']}", {
        "revision": 1, "corrected_text": "历史纠错保留", "reviewed": True})
    assert corrected.status_code == 200, corrected.text
    assert mutate(owner, "POST", "/ocr/documents", {**ocr_body, "request_key": str(uuid4()), "revision": 5}).status_code == 404
    assert owner.get(f"/api/v1/learning/cards/{card.json()['id']}/source").status_code == 200
    assert friend.get(f"/api/v1/learning/cards/{card.json()['id']}/source").status_code == 404
    active_pages = friend.get(f"/api/v1/social/shares/{grant['id']}").json()["pages"]
    assert [page["id"] for page in active_pages] == [second_id]
    assert friend.get(old_shared_url).status_code == 404
    for actor in (owner, friend):
        assert actor.get(f"/api/v1/offline/files/{first['transport']['file_id']}/content").status_code == 404
    login(client)
    assert mutate(client, "POST", f"/files/{first['transport']['file_id']}/ticket", {}).status_code == 404
    assert friend.get(independent[0]["original_url"]).content == retained_bytes
    current_post = published(owner, source_note_id=note_id, source_revision=5, plain_text="")
    assert len(friend.get(f"/api/v1/community/posts/{current_post['id']}").json()["assets"]) == 1
    result = mutate(owner, "POST", "/exports/archive", {})
    assert result.status_code == 200, result.text
    archive = tmp_path / "retired-linked-histories.zip"
    archive.write_bytes(result.content)
    assert restore_tool._load_verifier().verify_archive(archive)["note_pages"] == 2
    with engine.connect() as connection:
        assert retention.collect_references(connection)["keys"]


def test_page_retirement_empty_migration_cycle_and_downgrade_refuses_retained_content(client):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "f6ab70ad1456")
    command.upgrade(cfg, "head")
    command.check(cfg)
    login(client)
    note_id = new_note(client, "image")
    item = image_page(client, note_id)[2]
    sync(client, operation("page.remove", item["transport"]["page_id"], {"note_id": note_id}, 2))
    with pytest.raises(RuntimeError, match="page_retirement_downgrade_unsafe"):
        command.downgrade(cfg, "f6ab70ad1456")
    with engine.connect() as connection:
        from alembic.script import ScriptDirectory
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == ScriptDirectory.from_config(cfg).get_current_head()
        assert "deleted_at" in {column["name"] for column in inspect(connection).get_columns("note_pages")}


def test_image_active_limit_does_not_cap_frozen_citation_label_after_retirement(client, monkeypatch):
    from test_image_chat import image_book
    login(client)
    note_id = image_book(client, count=201)
    assert client.get(f"/api/v1/ai/image-notebooks/{note_id}").status_code == 422
    physical = client.get(f"/api/v1/notes/{note_id}/pages").json()
    assert len(physical) == 201
    sync(client, operation("page.remove", physical[0]["id"], {"note_id": note_id}, 2))
    detail = client.get(f"/api/v1/ai/image-notebooks/{note_id}")
    assert detail.status_code == 200, detail.text
    book = detail.json()
    assert book["page_count"] == 200
    page = book["pages"][-1]
    assert page["number"] == 201
    response = image_submit(client, note_id, image={"note_id": note_id, "revision": 3, "page_id": page["page_id"]})
    assert response.status_code == 201, response.text
    install_model_transport(monkeypatch, lambda request: httpx.Response(200, content=stream_bytes("保留原始图片标签 [[p:201]]")))
    assert chat_service.tick()
    run_id = response.json()["id"]
    result = client.get(f"/api/v1/ai/runs/{run_id}")
    assert result.status_code == 200 and result.json()["status"] == "succeeded"
    assert result.json()["source"]["pages"][0]["number"] == 201
    saved = mutate(client, "POST", f"/ai/runs/{run_id}/save-note", {"title": "图片201的历史回答"})
    assert saved.status_code == 201, saved.text
    assert saved.json()["cited_page_numbers"] == [201]
    with engine.connect() as connection:
        assert retention.collect_references(connection)["keys"]
