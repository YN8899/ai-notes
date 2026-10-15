from uuid import UUID
from fastapi.testclient import TestClient
from app.main import app
from app.shared.db import SessionLocal
from conftest import mutate, invite, register, login


def create(client, resource, **data):
    result = mutate(client, "POST", "/" + resource, data)
    assert result.status_code == 201, result.text
    return result.json()


def test_note_revision_versions_delete_and_restore(client):
    login(client)
    note = create(client, "notes", title="第一版", kind="text", body_json={"type": "doc", "content": []}, plain_text="正文")
    assert note["revision"] == 1
    path = "/notes/" + note["id"]
    changed = mutate(client, "PATCH", path, {"revision": 1, "title": "第二版"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["revision"] == 2
    assert changed.json()["plain_text"] == "正文"
    assert mutate(client, "PATCH", path, {"revision": 1, "title": "丢失更新"}).status_code == 409
    assert mutate(client, "DELETE", path, {"revision": 1}).status_code == 409
    assert mutate(client, "DELETE", path, {"revision": 2}).status_code == 204
    assert client.get("/api/v1" + path).status_code == 404
    assert client.get("/api/v1/notes").json()["total"] == 0
    trash = client.get("/api/v1/notes?deleted=true").json()["items"]
    assert trash[0]["revision"] == 3
    versions = client.get("/api/v1" + path + "/versions").json()
    assert [v["revision"] for v in versions] == [3, 2, 1]
    assert versions[-1]["snapshot"]["title"] == "第一版"
    assert versions[0]["snapshot"]["deleted_at"]
    restored = mutate(client, "POST", path + "/restore", {"revision": 3})
    assert restored.status_code == 200, restored.text
    assert restored.json()["revision"] == 4
    assert restored.json()["deleted_at"] is None


def test_owner_isolation_relations_and_admin(client):
    invitation = invite(client, 2)
    assert register(client, invitation["code"]).status_code == 201
    folder = create(client, "folders", name="私人")
    notebook = create(client, "notebooks", name="私人本", folder_id=folder["id"])
    tag = create(client, "tags", name="私人标签")
    note = create(client, "notes", title="私人笔记", notebook_id=notebook["id"], tag_ids=[tag["id"]])
    with TestClient(app) as other:
        assert register(other, invitation["code"], "bob").status_code == 201
        for resource, obj in [("folders", folder), ("notebooks", notebook), ("tags", tag), ("notes", note)]:
            path = "/" + resource + "/" + obj["id"]
            assert other.get("/api/v1" + path).status_code == 404
            assert mutate(other, "PATCH", path, {"revision": 1, "name": "盗改"} if resource != "notes" else {"revision": 1, "title": "盗改"}).status_code == 404
            assert mutate(other, "DELETE", path, {"revision": 1}).status_code == 404
        assert mutate(other, "POST", "/folders", {"name": "非法", "parent_id": folder["id"]}).status_code == 404
        assert mutate(other, "POST", "/notebooks", {"name": "非法", "folder_id": folder["id"]}).status_code == 404
        assert mutate(other, "POST", "/notes", {"title": "非法", "notebook_id": notebook["id"]}).status_code == 404
        assert mutate(other, "POST", "/notes", {"title": "非法", "tag_ids": [tag["id"]]}).status_code == 404
    with TestClient(app) as admin:
        login(admin)
        assert admin.get("/api/v1/notes/" + note["id"]).status_code == 404
        assert admin.get("/api/v1/notes/" + note["id"] + "/versions").status_code == 404
        assert admin.get("/api/v1/notes").json()["total"] == 0


def test_folder_cycles_moves_and_nonempty_delete(client):
    login(client)
    root = create(client, "folders", name="根")
    child = create(client, "folders", name="子", parent_id=root["id"])
    leaf = create(client, "folders", name="孙", parent_id=child["id"])
    path = "/folders/" + root["id"]
    for target in (root["id"], leaf["id"]):
        assert mutate(client, "PATCH", path, {"revision": 1, "parent_id": target}).status_code == 409
    assert mutate(client, "DELETE", path, {"revision": 1}).status_code == 409
    result = mutate(client, "PATCH", "/folders/" + child["id"], {"revision": 1, "parent_id": None})
    assert result.status_code == 200, result.text
    assert result.json()["parent_id"] is None
    assert mutate(client, "DELETE", path, {"revision": 1}).status_code == 204


def test_notebook_revision_and_tag_uniqueness(client):
    login(client)
    notebook = create(client, "notebooks", name="学习")
    tag = create(client, "tags", name="数学")
    assert mutate(client, "POST", "/tags", {"name": "数学"}).status_code == 409
    path = "/notebooks/" + notebook["id"]
    assert mutate(client, "PATCH", path, {"revision": 1, "name": "新本"}).json()["revision"] == 2
    assert mutate(client, "DELETE", path, {"revision": 1}).status_code == 409
    note = create(client, "notes", title="保护关联", notebook_id=notebook["id"], tag_ids=[tag["id"]])
    assert mutate(client, "DELETE", path, {"revision": 2}).status_code == 409
    assert mutate(client, "DELETE", "/tags/" + tag["id"], {"revision": 1}).status_code == 409
    assert mutate(client, "PATCH", "/notes/" + note["id"], {"revision": 1, "tag_ids": [], "notebook_id": None}).status_code == 200
    assert mutate(client, "DELETE", "/tags/" + tag["id"], {"revision": 1}).status_code == 204


def test_filters_pagination_and_note_kind(client):
    login(client)
    folder = create(client, "folders", name="课程")
    notebook = create(client, "notebooks", name="高数", folder_id=folder["id"])
    tag = create(client, "tags", name="重点")
    first = create(client, "notes", title="微积分", kind="text", notebook_id=notebook["id"], tag_ids=[tag["id"]], plain_text="函数极限")
    create(client, "notes", title="照片", kind="image")
    listing = client.get("/api/v1/notes?limit=1&offset=1").json()
    assert listing["total"] == 2 and len(listing["items"]) == 1
    for query in ("q=极限", "kind=text", "tag_id=" + tag["id"], "notebook_id=" + notebook["id"], "folder_id=" + folder["id"]):
        result = client.get("/api/v1/notes?" + query).json()
        assert result["total"] == 1 and result["items"][0]["id"] == first["id"]
    assert "body_json" not in listing["items"][0]


def test_invalid_patch_and_image_body_are_rejected(client):
    login(client)
    note = create(client, "notes", title="有效")
    path = "/notes/" + note["id"]
    for changes in ({"title": None}, {"title": " "}, {"kind": "image"}, {"tag_ids": None}, {"body_json": None}, {"plain_text": None}, {"unknown": "ignored"}):
        assert mutate(client, "PATCH", path, {"revision": 1, **changes}).status_code == 422
    assert mutate(client, "PATCH", path, {"title": "没有版本"}).status_code == 422
    assert mutate(client, "POST", "/notes", {"title": "图片", "kind": "image", "body_json": {"type": "doc"}}).status_code == 422
    assert mutate(client, "POST", "/folders", {"name": None}).status_code == 422


def test_database_rejects_cross_owner_relations(client):
    invitation = invite(client, 2)
    register(client, invitation["code"])
    folder = create(client, "folders", name="Alice")
    notebook_alice = create(client, "notebooks", name="Alice")
    tag_alice = create(client, "tags", name="Alice")
    note_alice = create(client, "notes", title="Alice")
    with TestClient(app) as other:
        register(other, invitation["code"], "bob")
        notebook = create(other, "notebooks", name="Bob")
        folder_bob = create(other, "folders", name="Bob")
        tag_bob = create(other, "tags", name="Bob")
        note_bob = create(other, "notes", title="Bob")
    from sqlalchemy.exc import IntegrityError
    from app.modules.notes.models import Folder, Notebook, Note, NoteTag, NoteVersion
    import pytest
    for model, resource_id, field, foreign_id in [(Notebook, notebook["id"], "folder_id", folder["id"]), (Folder, folder_bob["id"], "parent_id", folder["id"]), (Note, note_bob["id"], "notebook_id", notebook_alice["id"])]:
        with SessionLocal() as db:
            row = db.get(model, UUID(resource_id))
            setattr(row, field, UUID(foreign_id))
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()
    for row in [NoteTag(owner_id=UUID(note_bob["owner_id"]), note_id=UUID(note_bob["id"]), tag_id=UUID(tag_alice["id"])), NoteTag(owner_id=UUID(note_alice["owner_id"]), note_id=UUID(note_bob["id"]), tag_id=UUID(tag_alice["id"])), NoteVersion(owner_id=UUID(note_bob["owner_id"]), note_id=UUID(note_alice["id"]), revision=7, snapshot={})]:
        with SessionLocal() as db:
            db.add(row)
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()


def test_concurrent_note_saves_only_one_revision_wins(client):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    login(client)
    note = create(client, "notes", title="初版")
    barrier = threading.Barrier(2)

    def save(title):
        with TestClient(app) as device:
            login(device)
            barrier.wait(timeout=10)
            return mutate(device, "PATCH", "/notes/" + note["id"], {"revision": 1, "title": title}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["设备一", "设备二"]))
    assert sorted(results) == [200, 409]
    stored = client.get("/api/v1/notes/" + note["id"]).json()
    assert stored["revision"] == 2 and stored["title"] in {"设备一", "设备二"}
    versions = client.get("/api/v1/notes/" + note["id"] + "/versions").json()
    assert len(versions) == 2 and versions[0]["snapshot"]["title"] == stored["title"]


def test_concurrent_folder_moves_cannot_create_cycle(client):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    login(client)
    first = create(client, "folders", name="甲")
    second = create(client, "folders", name="乙")
    barrier = threading.Barrier(2)

    def move(pair):
        with TestClient(app) as device:
            login(device)
            barrier.wait(timeout=10)
            return mutate(device, "PATCH", "/folders/" + pair[0], {"revision": 1, "parent_id": pair[1]}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(move, [(first["id"], second["id"]), (second["id"], first["id"])]))
    assert sorted(results) == [200, 409]
    rows = client.get("/api/v1/folders").json()
    assert len([row for row in rows if row["parent_id"] is None]) == 1


def test_note_body_structure_and_byte_budget(client):
    login(client)
    for body in ({"type": "paragraph", "content": []}, {"type": "doc"}, {"type": "doc", "content": {}}, {"type": "doc", "content": [{"text": "中" * 350_000}]}):
        result = mutate(client, "POST", "/notes", {"title": "无效正文", "body_json": body})
        assert result.status_code == 422, result.text
    assert mutate(client, "POST", "/notes", {"title": "过长正文", "plain_text": "中" * 350_000}).status_code == 422
    deep = {"type": "doc", "content": []}
    for _ in range(70):
        deep = {"type": "doc", "content": [deep]}
    assert mutate(client, "POST", "/notes", {"title": "过深正文", "body_json": deep}).status_code == 422
    assert mutate(client, "POST", "/notes", {"title": "节点过多", "body_json": {"type": "doc", "content": [0] * 50_001}}).status_code == 422
    note = create(client, "notes", title="合并限制", body_json={"type": "doc", "content": [{"type": "text", "text": "a" * 600_000}]})
    result = mutate(client, "PATCH", "/notes/" + note["id"], {"revision": 1, "plain_text": "a" * 600_000})
    assert result.status_code == 422, result.text
    stored = client.get("/api/v1/notes/" + note["id"]).json()
    assert stored["revision"] == 1 and stored["plain_text"] == ""


def test_note_cursor_ignores_new_writes_and_checks_scope(client):
    login(client)
    oldest = create(client, "notes", title="oldest")
    middle = create(client, "notes", title="middle")
    newest = create(client, "notes", title="newest")
    first = client.get("/api/v1/notes?limit=1").json()
    assert first["items"][0]["id"] == newest["id"]
    cursor = first["next_cursor"]
    assert cursor
    inserted = create(client, "notes", title="inserted after first page")
    assert mutate(client, "PATCH", "/notes/" + newest["id"], {"revision": 1, "title": "updated after first page"}).status_code == 200
    second = client.get("/api/v1/notes", params={"limit": 1, "cursor": cursor}).json()
    assert second["items"][0]["id"] == middle["id"]
    third = client.get("/api/v1/notes", params={"limit": 1, "cursor": second["next_cursor"]}).json()
    assert third["items"][0]["id"] == oldest["id"] and third["next_cursor"] is None
    assert inserted["id"] not in {page["items"][0]["id"] for page in [first, second, third]}
    assert client.get("/api/v1/notes", params={"cursor": cursor, "kind": "image"}).status_code == 422
    assert client.get("/api/v1/notes", params={"cursor": cursor, "offset": 1}).status_code == 422
    for invalid in ["bad!sensitive-token", "e30", "", "a" * 1025]:
        response = client.get("/api/v1/notes", params={"cursor": invalid})
        assert response.status_code == 422
        if invalid:
            assert invalid not in response.text
    import base64
    import json
    decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    for field, value in [("id", "not-a-uuid"), ("at", "2026-10-14T00:00:00"), ("cutoff", "2999-01-01T00:00:00+00:00"), ("v", True)]:
        tampered = base64.urlsafe_b64encode(json.dumps({**decoded, field: value}).encode()).decode().rstrip("=")
        assert client.get("/api/v1/notes", params={"cursor": tampered}).status_code == 422


def test_note_cursor_uuid_tiebreak_and_owner_binding(client):
    login(client)
    notes = [create(client, "notes", title="同一时间" + str(index)) for index in range(3)]
    from app.modules.notes.models import Note
    from app.shared.db import utcnow
    timestamp = utcnow()
    with SessionLocal.begin() as db:
        for item in notes:
            db.get(Note, UUID(item["id"])).updated_at = timestamp
    first = client.get("/api/v1/notes?limit=1").json()
    second = client.get("/api/v1/notes", params={"limit": 1, "cursor": first["next_cursor"]}).json()
    third = client.get("/api/v1/notes", params={"limit": 1, "cursor": second["next_cursor"]}).json()
    assert [page["items"][0]["id"] for page in [first, second, third]] == sorted([item["id"] for item in notes], reverse=True)
    assert third["next_cursor"] is None
    invitation = invite(client)
    with TestClient(app) as other:
        assert register(other, invitation["code"]).status_code == 201
        response = other.get("/api/v1/notes", params={"cursor": first["next_cursor"]})
        assert response.status_code == 422


def test_versions_cursor_excludes_new_revisions(client):
    login(client)
    note = create(client, "notes", title="v1")
    for revision in (1, 2):
        assert mutate(client, "PATCH", "/notes/" + note["id"], {"revision": revision, "title": "v" + str(revision + 1)}).status_code == 200
    path = "/api/v1/notes/" + note["id"] + "/versions/page"
    first = client.get(path, params={"limit": 1}).json()
    assert first["items"][0]["revision"] == 3 and first["next_cursor"]
    assert mutate(client, "PATCH", "/notes/" + note["id"], {"revision": 3, "title": "v4"}).status_code == 200
    second = client.get(path, params={"limit": 1, "cursor": first["next_cursor"]}).json()
    third = client.get(path, params={"limit": 1, "cursor": second["next_cursor"]}).json()
    assert [second["items"][0]["revision"], third["items"][0]["revision"]] == [2, 1]
    assert third["next_cursor"] is None and third["total"] == 3
    assert client.get(path, params={"cursor": "not-valid"}).status_code == 422


def test_database_unsupported_text_is_rejected_and_redacted(client):
    import json
    login(client)

    def request(method, path, body):
        csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        # ASCII JSON escapes let an unpaired surrogate reach server validation;
        # httpx's normal json= encoder otherwise rejects it in the test client.
        return client.request(method, "/api/v1" + path, content=json.dumps(body, ensure_ascii=True), headers={"Origin": "http://testserver", "X-CSRF-Token": csrf, "Content-Type": "application/json"})

    existing = {resource: create(client, resource, **({"title": "有效笔记"} if resource == "notes" else {"name": "有效"})) for resource in ["folders", "notebooks", "tags", "notes"]}
    for invalid in ("private\x00content", "private\ud800content"):
        for resource in ("folders", "notebooks", "tags", "notes"):
            field = "title" if resource == "notes" else "name"
            for method, path, body in [("POST", "/" + resource, {field: invalid}), ("PATCH", "/" + resource + "/" + existing[resource]["id"], {"revision": 1, field: invalid})]:
                response = request(method, path, body)
                assert response.status_code == 422, response.text
                assert "private" not in response.text
        for value in ({"plain_text": invalid}, {"body_json": {"type": "doc", "content": [{"type": "text", "text": invalid}]}}, {"body_json": {"type": "doc", "content": [{"attrs": {invalid: "value"}}]}}):
            for method, path, body in [("POST", "/notes", {"title": "无效内容", **value}), ("PATCH", "/notes/" + existing["notes"]["id"], {"revision": 1, **value})]:
                response = request(method, path, body)
                assert response.status_code == 422, response.text
                assert "private" not in response.text
    for resource, item in existing.items():
        assert client.get("/api/v1/" + resource + "/" + item["id"]).json()["revision"] == 1
    versions = client.get("/api/v1/notes/" + existing["notes"]["id"] + "/versions").json()
    assert len(versions) == 1 and versions[0]["revision"] == 1
    valid = create(client, "notes", title="正常换行", body_json={"type": "doc", "content": [{"type": "text", "text": "第一行\n第二行\t表格"}]}, plain_text="第一行\n第二行\t表格")
    response = mutate(client, "PATCH", "/notes/" + valid["id"], {"revision": 1, "plain_text": "新的第一行\n第二行"})
    assert response.status_code == 200 and response.json()["plain_text"] == "新的第一行\n第二行"
    response = client.get("/api/v1/notes", params={"q": "private\x00content"})
    assert response.status_code == 422 and "private" not in response.text
