"""Private cards: real PostgreSQL, synthetic sources, never live provider calls."""
import pytest
import json
from datetime import timedelta
from uuid import UUID, uuid4
from pydantic import ValidationError
from sqlalchemy import func, select
from app.modules.ai import chat_provider, chat_service
from app.modules.ai.models import AiRun
from app.modules.files.models import File, NotePage
from app.modules.learning.models import Card, CardGeneration
from app.modules.learning import card_generation
from app.modules.notes.models import Note
from app.shared.config import settings
from app.shared.db import SessionLocal, utcnow
from app.shared.models import User
from app.shared.security import hasher
from conftest import login, mutate
from test_text_selection import note as text_note, document
from test_image_chat import image_book, image_source, mock
from test_pdf_chat import pdf_book


@pytest.fixture(autouse=True)
def private_config(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "cards-fake-test-key")
    settings.cache_clear()
    monkeypatch.setattr(settings(), "storage_root", str(tmp_path / "private"))
    monkeypatch.setattr(chat_provider, "create_client", lambda: (_ for _ in ()).throw(AssertionError("No live provider")))
    yield
    settings.cache_clear()


def deck(client):
    result = mutate(client, "POST", "/learning/decks", {"name": "数学"})
    assert result.status_code == 201, result.text
    return result.json()


def create_card(client, did, **changes):
    return mutate(client, "POST", "/learning/cards", {"deck_id": did, "kind": "qa", "front": "问题", "back": "答案", **changes})


def generation_body(client, *, kind="qa", count=1, source=None):
    if source is None:
        _, selection = text_note(client)
        source = {"kind": "text", **selection}
    body = {"deck_id": deck(client)["id"], "kind": kind, "count": count, "source": source}
    preview = mutate(client, "POST", "/learning/card-generations/preview", body)
    assert preview.status_code == 200, preview.text
    return {**body, "preview_hash": preview.json()["preview_hash"], "request_key": str(uuid4()), "confirm_external": True}


def gen_create(client, body):
    result = mutate(client, "POST", "/learning/card-generations", body)
    assert result.status_code == 201, result.text
    return result.json()


def gen_read(client, gid):
    result = client.get("/api/v1/learning/card-generations/" + gid)
    assert result.status_code == 200, result.text
    return result.json()


def test_card_contract_rejects_empty_cloze_and_unknown_fields():
    from app.modules.learning.card_schemas import CardContent
    for value in [dict(kind="cloze", front="{{ }}", back=""),
                  dict(kind="qa", front="q", back="a", source_id="invented")]:
        with pytest.raises(ValidationError):
            CardContent.model_validate(value)


def test_generation_json_is_exact_and_count_bounded():
    from app.modules.learning.card_generation import decode_cards
    assert len(decode_cards('{"cards":[{"kind":"qa","front":"q","back":"a"}]}', "qa", 1)) == 1
    for answer in ['prefix {"cards":[]}', '{"cards":[]}', '{"cards":[{"kind":"qa","front":"q","back":"a","source_id":"fake"}]}',
                   '{"cards":[],"cards":[]}', '{"cards":[{"kind":"qa","front":"q","front":"new","back":"a"}]}',
                   '{"cards":[{"kind":"qa","front":true,"back":"a"}]}', '{"cards":[{"kind":"qa","front":"q","back":"a","payload":{"masks":[{"x":NaN}]}}]}',
                   '```json\n{"cards":[{"kind":"qa","front":"q","back":"a"}]}\n``` suffix', '[' * 2000]:
        with pytest.raises(ValueError):
            decode_cards(answer, "qa", 1)


@pytest.mark.parametrize("kind,front,back", [("qa", "q", "a"), ("cloze", "水是{{液体}}", "说明"), ("formula", r"E=mc^2", "")])
def test_manual_types_revision_filters_archive(client, kind, front, back):
    login(client)
    d = deck(client)
    result = create_card(client, d["id"], kind=kind, front=front, back=back)
    assert result.status_code == 201, result.text
    card = result.json()
    assert card["status"] == "draft" and card["revision"] == 1 and card["source"] is None
    changed = mutate(client, "PATCH", "/learning/cards/" + card["id"], {"revision": 1, "front": front + "新", "status": "confirmed"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["revision"] == 2
    assert mutate(client, "PATCH", "/learning/cards/" + card["id"], {"revision": 1, "back": "旧"}).status_code == 409
    assert client.get("/api/v1/learning/cards", params={"kind": kind, "status": "draft"}).json()["total"] == 0
    assert client.get("/api/v1/learning/cards", params={"kind": kind, "status": "confirmed", "limit": 1}).json()["total"] == 1
    assert mutate(client, "PATCH", "/learning/decks/" + d["id"], {"revision": 1, "status": "archived"}).status_code == 200
    assert create_card(client, d["id"]).status_code == 409
    assert mutate(client, "PATCH", "/learning/cards/" + card["id"], {"revision": 2, "status": "archived"}).status_code == 200
    assert mutate(client, "PATCH", "/learning/decks/" + d["id"], {"revision": 2, "name": "恢复", "status": "active"}).status_code == 200


@pytest.mark.parametrize("changes", [{"kind": "cloze", "front": "{{}}"}, {"kind": "cloze", "front": "{{{a}}}"}, {"kind": "cloze", "front": "{{a}} {{b"}, {"kind": "qa", "back": " "}, {"front": "x" * 8001}, {"payload": {"masks": []}}, {"unknown": "x"}, {"kind": "occlusion", "payload": {"masks": [{"x": .9, "y": 0, "width": .2, "height": 1}]}}])
def test_bad_cards_rejected(client, changes):
    login(client)
    assert create_card(client, deck(client)["id"], **changes).status_code == 422


def test_batch_atomic_limit_duplicates_and_ownership(client):
    login(client)
    did = deck(client)["id"]
    rows = [create_card(client, did).json() for _ in range(2)]
    items = [{"id": row["id"], "revision": 1} for row in rows]
    endpoint = "/learning/cards/batch-review"
    assert mutate(client, "POST", endpoint, {"action": "confirm", "cards": [items[0], items[0]]}).status_code == 422
    assert mutate(client, "POST", endpoint, {"action": "confirm", "cards": [{"id": str(uuid4()), "revision": 1}] * 51}).status_code == 422
    assert mutate(client, "POST", endpoint, {"action": "confirm", "cards": [items[0], {**items[1], "revision": 2}]}).status_code == 409
    assert all(row["status"] == "draft" for row in client.get("/api/v1/learning/cards").json()["items"])
    assert mutate(client, "POST", endpoint, {"action": "confirm", "cards": [items[0], {"id": str(uuid4()), "revision": 1}]}).status_code == 404
    done = mutate(client, "POST", endpoint, {"action": "confirm", "cards": items})
    assert done.status_code == 200 and all(row["revision"] == 2 for row in done.json())
    with SessionLocal.begin() as db:
        user = User(username="other", display_name="other", password_hash=hasher.hash("member-password"))
        db.add(user)
        db.flush()
        other = user.id
    from app.modules.learning.models import Deck
    with SessionLocal.begin() as db:
        foreign = Deck(owner_id=other, name="私有")
        db.add(foreign)
        db.flush()
        foreign_id = str(foreign.id)
    assert create_card(client, foreign_id).status_code == 404
    login(client, "other", "member-password")
    assert client.get("/api/v1/learning/cards/" + rows[0]["id"]).status_code == 404
    assert mutate(client, "POST", endpoint, {"action": "archive", "cards": [{"id": rows[0]["id"], "revision": 2}]}).status_code == 404


def test_text_source_history_and_deleted_note(client):
    login(client)
    original, selection = text_note(client, "历史来源")
    row = create_card(client, deck(client)["id"], source={"kind": "text", **selection}).json()
    response = mutate(client, "PATCH", "/notes/" + original["id"], {"revision": original["revision"], "title": "新标题", "body_json": document("新内容")})
    assert response.status_code == 200, response.text
    read = client.get("/api/v1/learning/cards/" + row["id"] + "/source")
    assert read.status_code == 200, read.text
    assert read.json()["text"] == "历史来源" and read.json()["source_changed"]
    assert read.json()["source"]["title"] == original["title"]
    with SessionLocal.begin() as db:
        db.get(Note, UUID(original["id"])).deleted_at = utcnow()
    assert client.get("/api/v1/learning/cards/" + row["id"] + "/source").status_code == 409
    retained = client.get("/api/v1/learning/cards/" + row["id"]).json()
    assert retained["front"] == "问题" and not retained["source_valid"]


@pytest.mark.parametrize("pdf", [False, True])
def test_visual_occlusion_source_and_bounds(client, pdf):
    login(client)
    nid = pdf_book(client) if pdf else image_book(client)
    if pdf:
        detail = client.get("/api/v1/ai/pdf-notebooks/" + nid).json()
        source = {"kind": "pdf", "note_id": nid, "revision": detail["revision"], "mode": "region", "page_numbers": [1], "region": {"x": .5, "y": 0, "width": .5, "height": 1}}
    else:
        source = {"kind": "image", **image_source(client, nid, region={"x": .5, "y": 0, "width": .5, "height": 1})}
    did = deck(client)["id"]
    masks = {"masks": [{"x": .5, "y": .1, "width": .2, "height": .2}]}
    response = create_card(client, did, kind="occlusion", source=source, payload=masks)
    assert response.status_code == 201, response.text
    row = response.json()
    assert "object_key" not in response.text and "reading_sha256" not in response.text
    masks["masks"][0]["x"] = .1
    assert create_card(client, did, kind="occlusion", source=source, payload=masks).status_code == 422
    result = client.get("/api/v1/learning/cards/" + row["id"] + "/source")
    assert result.status_code == 200 and result.json()["file_id"] and result.json()["page_number"] == 1
    with SessionLocal.begin() as db:
        db.get(File, UUID(result.json()["file_id"])).sha256 = "b" * 64
    assert client.get("/api/v1/learning/cards/" + row["id"] + "/source").status_code == 409


def test_generation_idempotent_private_and_worker_drafts(client, monkeypatch):
    login(client)
    body = generation_body(client)
    row = gen_create(client, body)
    assert gen_create(client, body)["id"] == row["id"]
    assert mutate(client, "POST", "/learning/card-generations", {**body, "count": 2}).status_code == 409
    assert client.get("/api/v1/ai/conversations").json() == []
    with SessionLocal() as db:
        generation = db.get(CardGeneration, UUID(row["id"]))
        run = db.get(AiRun, generation.run_id)
        cid = str(run.conversation_id)
        from app.modules.ai.models import AiConversation
        conversation_key = str(db.get(AiConversation, run.conversation_id).request_key)
    assert client.get("/api/v1/ai/conversations/" + cid).status_code == 404
    assert mutate(client, "POST", "/ai/conversations", {"request_key": conversation_key}).status_code == 404
    assert mutate(client, "POST", "/ai/conversations/" + cid + "/runs", {"request_key": str(uuid4()), "prompt": "append"}).status_code == 404
    calls = mock(monkeypatch, answer='```json\n{"cards":[{"kind":"qa","front":"Q","back":"A"}]}\n```')
    assert gen_read(client, row["id"])["status"] == "pending" and not calls
    assert chat_service.tick()
    done = gen_read(client, row["id"])
    assert done["status"] == "succeeded" and len(done["card_ids"]) == 1 and len(calls) == 1
    card = client.get("/api/v1/learning/cards/" + done["card_ids"][0]).json()
    assert card["status"] == "draft" and card["source"] == done["source"]
    assert gen_read(client, row["id"])["card_ids"] == done["card_ids"]
    assert not card_generation.tick() and not chat_service.tick() and len(calls) == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Card)) == 1


@pytest.mark.parametrize("answer", ['oops', '{"cards":[]}', '{"cards":[{"kind":"qa","front":"Q","back":"A","source_id":"invented"}]}', '{"cards":[{"kind":"cloze","front":"{{ }}","back":""}]}', '{"cards":[{"kind":"qa","front":"Q","back":"A"},{"kind":"qa","front":"Q","back":"A"}]}'])
def test_invalid_generation_all_or_nothing_preserves_usage(client, monkeypatch, answer):
    login(client)
    row = gen_create(client, generation_body(client))
    calls = mock(monkeypatch, answer=answer)
    assert chat_service.tick()
    result = gen_read(client, row["id"])
    assert result["status"] == "failed" and result["error_code"] == "card_output_invalid" and result["card_ids"] == []
    assert result["usage"] and len(calls) == 1
    assert not chat_service.tick()
    assert client.get("/api/v1/learning/cards").json()["total"] == 0


@pytest.mark.parametrize("state", ["interrupted", "cancelled", "failed", "truncated", "stale_lease", "disabled", "deleted", "changed_source"])
def test_materialization_terminal_owner_and_source_guards(client, state):
    login(client)
    row = gen_create(client, generation_body(client))
    with SessionLocal.begin() as db:
        gen = db.get(CardGeneration, UUID(row["id"]))
        run = db.get(AiRun, gen.run_id)
        run.status = state if state in {"interrupted", "cancelled", "failed", "truncated"} else "succeeded"
        run.answer = '{"cards":[{"kind":"qa","front":"Q","back":"A"}]}'
        run.usage = {"total_tokens": 12}
        run.finished_at = utcnow()
        if state == "stale_lease": run.lease_until = utcnow() - timedelta(seconds=1)
        if state == "disabled": db.get(User, run.owner_id).status = "disabled"
        if state == "deleted": db.get(Note, UUID(gen.source["note_id"])).deleted_at = utcnow()
        if state == "changed_source": run.source = {**run.source, "quote": "伪造"}
    assert card_generation.tick()
    with SessionLocal() as db:
        gen = db.get(CardGeneration, UUID(row["id"]))
        assert gen.status == "failed"
        assert db.scalar(select(func.count()).select_from(Card)) == 0
        assert db.get(AiRun, gen.run_id).usage == {"total_tokens": 12}


@pytest.mark.parametrize("pdf", [False, True])
@pytest.mark.parametrize("crop", [False, True])
def test_visual_generation_real_payload_and_source_change_after_call(client, monkeypatch, pdf, crop):
    from test_image_chat import image_parts, decoded_image
    login(client)
    nid = pdf_book(client) if pdf else image_book(client)
    region = {"x": .5, "y": 0, "width": .5, "height": 1} if crop else None
    if pdf:
        detail = client.get("/api/v1/ai/pdf-notebooks/" + nid).json()
        source = {"kind": "pdf", "note_id": nid, "revision": detail["revision"], "mode": "region" if crop else "pages", "page_numbers": [1], "region": region}
    else:
        source = {"kind": "image", **image_source(client, nid, region=region)}
    row = gen_create(client, generation_body(client, source=source, kind="occlusion"))
    answer = json.dumps({"cards": [{"kind": "occlusion", "front": "内容", "back": "答案", "payload": {"masks": [{"x": .6, "y": .1, "width": .2, "height": .2}]}}]})
    calls = mock(monkeypatch, answer=answer)
    assert chat_service.tick()
    result = gen_read(client, row["id"])
    assert result["status"] == "succeeded", result
    assert len(calls) == 1 and "image_url" in str(calls[0])
    assert "并标记来源页码" not in str(calls[0])
    image = decoded_image(image_parts(calls[0])[0])
    assert image.width == (20 if crop else 40) if pdf else image.width == (200 if crop else 400)


@pytest.mark.parametrize("kind,front", [("cloze", "地球是{{行星}}"), ("formula", r"E=mc^2")])
def test_generation_editable_non_qa_and_maximum_count(client, monkeypatch, kind, front):
    login(client)
    body = generation_body(client, kind=kind, count=20)
    row = gen_create(client, body)
    calls = mock(monkeypatch, answer=json.dumps({"cards": [{"kind": kind, "front": front, "back": "解释"} for _ in range(20)]}, ensure_ascii=False))
    assert chat_service.tick()
    done = gen_read(client, row["id"])
    assert done["status"] == "succeeded" and len(done["card_ids"]) == 20 and len(set(done["card_ids"])) == 20
    assert all(card["status"] == "draft" for card in client.get("/api/v1/learning/cards").json()["items"])
    assert len(calls) == 1


def test_preview_staleness_no_fee_and_request_bounds(client, monkeypatch):
    login(client)
    body = generation_body(client)
    assert mutate(client, "POST", "/learning/card-generations", {**body, "preview_hash": "a" * 64}).status_code == 409
    for change in [{"confirm_external": False}, {"confirm_external": 1}, {"count": 21}, {"count": True}, {"source": {**body["source"], "object_key": "secret"}}]:
        assert mutate(client, "POST", "/learning/card-generations", {**body, **change}).status_code == 422
    assert client.get("/api/v1/learning/cards", params={"limit": 51}).status_code == 422
    assert client.get("/api/v1/learning/card-generations").json()["total"] == 0


def test_batch_fifty_atomic_and_whole_batch_stale(client):
    login(client)
    did = deck(client)["id"]
    rows = [create_card(client, did).json() for _ in range(50)]
    items = [{"id": row["id"], "revision": 1} for row in rows]
    endpoint = "/learning/cards/batch-review"
    assert mutate(client, "PATCH", "/learning/cards/" + rows[-1]["id"], {"revision": 1, "back": "new"}).status_code == 200
    assert mutate(client, "POST", endpoint, {"action": "confirm", "cards": items}).status_code == 409
    assert client.get("/api/v1/learning/cards", params={"status": "draft"}).json()["total"] == 50
    items[-1]["revision"] = 2
    result = mutate(client, "POST", endpoint, {"action": "confirm", "cards": items})
    assert result.status_code == 200 and len(result.json()) == 50


def test_visual_source_reorder_uses_stable_page_and_reading_hash(client):
    from app.modules.files.models import FileVariant
    from app.modules.files import service as files
    login(client)
    nid = image_book(client)
    source = {"kind": "image", **image_source(client, nid)}
    row = create_card(client, deck(client)["id"], source=source).json()
    with SessionLocal.begin() as db:
        pages = list(db.scalars(select(NotePage).where(NotePage.note_id == UUID(nid)).order_by(NotePage.position)))
        pages[0].position = 99
        db.flush()
        pages[1].position = 0
        db.flush()
        pages[0].position = 1
        db.get(Note, UUID(nid)).revision += 1
    result = client.get("/api/v1/learning/cards/" + row["id"] + "/source")
    assert result.status_code == 200 and result.json()["page_id"] == source["page_id"]
    assert result.json()["page_number"] == 1 and result.json()["source_changed"]
    with SessionLocal() as db:
        variant = db.scalar(select(FileVariant).where(FileVariant.file_id == UUID(result.json()["file_id"])))
        key = variant.object_key
    data = files.storage().read(key)
    # Same-sized replacement retaining valid JPEG structure must still fail its hash.
    changed = bytearray(data)
    changed[-3] ^= 1
    files.storage().put(key, bytes(changed))
    assert client.get("/api/v1/learning/cards/" + row["id"] + "/source").status_code == 409


@pytest.mark.parametrize("visual", [False, True])
def test_source_lost_during_real_mock_stream_never_materializes(client, monkeypatch, visual):
    import httpx
    from test_ai_chat import stream_bytes
    login(client)
    if visual:
        nid = image_book(client)
        source = {"kind": "image", **image_source(client, nid)}
    else:
        original, selection = text_note(client)
        nid, source = original["id"], {"kind": "text", **selection}
    row = gen_create(client, generation_body(client, source=source))
    def removed(_):
        with SessionLocal.begin() as db:
            db.get(Note, UUID(nid)).deleted_at = utcnow()
        # Usage known before access-loss detection must survive the failure.
        event = {"choices": [{"delta": {"content": '{"cards":[{"kind":"qa","front":"Q","back":"A"}]}'}, "finish_reason": "stop"}], "usage": {"total_tokens": 18}}
        return httpx.Response(200, content=b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n")
    calls = mock(monkeypatch, handler=removed)
    assert chat_service.tick()
    result = gen_read(client, row["id"])
    assert result["status"] == "failed" and result["card_ids"] == []
    assert result["usage"]["total_tokens"] == 18 and len(calls) == 1
    assert not chat_service.tick()


def test_lost_worker_lease_is_failed_without_provider_replay(client, monkeypatch):
    login(client)
    row = gen_create(client, generation_body(client))
    with SessionLocal.begin() as db:
        gen = db.get(CardGeneration, UUID(row["id"]))
        run = db.get(AiRun, gen.run_id)
        run.status, run.lease_until = "running", utcnow() - timedelta(seconds=1)
        run.usage = {"total_tokens": 7}
    calls = mock(monkeypatch)
    assert not chat_service.tick()
    result = gen_read(client, row["id"])
    assert result["status"] == "failed" and result["run_status"] == "interrupted"
    assert result["usage"] == {"total_tokens": 7} and not calls


def test_provider_unknown_result_never_paid_replays(client, monkeypatch):
    import httpx
    login(client)
    body = generation_body(client)
    row = gen_create(client, body)
    def lost(_):
        raise httpx.ReadTimeout("simulated uncertain response")
    calls = mock(monkeypatch, handler=lost)
    assert chat_service.tick()
    result = gen_read(client, row["id"])
    assert result["status"] == "failed" and not result["card_ids"] and len(calls) == 1
    assert gen_create(client, body)["id"] == row["id"]
    assert not chat_service.tick() and len(calls) == 1


def test_materialization_refreshes_preloaded_disabled_user(client):
    login(client)
    row = gen_create(client, generation_body(client))
    with SessionLocal() as db:
        gen = db.get(CardGeneration, UUID(row["id"]))
        user = db.get(User, gen.owner_id)
        assert user.status == "active"
        with SessionLocal.begin() as other:
            other.get(User, user.id).status = "disabled"
            run = other.get(AiRun, gen.run_id)
            run.status, run.finished_at = "succeeded", utcnow()
            run.answer = '{"cards":[{"kind":"qa","front":"Q","back":"A"}]}'
        result = card_generation.materialize(db, gen.id)
        assert result.status == "failed" and result.error_code == "account_disabled"
        db.commit()


def test_source_cross_owner_forgery_and_multipage_rejected(client):
    login(client)
    original, selection = text_note(client)
    did = deck(client)["id"]
    for source in [{"kind": "text", **selection, "quote": "伪造选段"},
                   {"kind": "text", **selection, "url": "https://external.invalid"},
                   {"kind": "pdf", "note_id": original["id"], "revision": 1, "mode": "pages", "page_numbers": [1, 2]}]:
        assert create_card(client, did, source=source).status_code == 422
    with SessionLocal.begin() as db:
        user = User(username="other", display_name="other", password_hash=hasher.hash("member-password"))
        db.add(user)
    login(client, "other", "member-password")
    assert create_card(client, deck(client)["id"], source={"kind": "text", **selection}).status_code == 404


def test_database_composite_owner_guard(client):
    from sqlalchemy.exc import IntegrityError
    from app.modules.learning.models import Deck
    login(client)
    own = deck(client)
    with SessionLocal.begin() as db:
        other = User(username="other", display_name="other", password_hash="unused")
        db.add(other)
        db.flush()
        oid = other.id
    with pytest.raises(IntegrityError):
        with SessionLocal.begin() as db:
            db.add(Card(owner_id=oid, deck_id=UUID(own["id"]), kind="qa", front="q", back="a"))
