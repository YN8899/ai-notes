from app.shared import outbound
from model_transport import install_model_transport as _install_model_transport
"""Real disposable PostgreSQL/pgvector; every model response is an in-process fake."""
from copy import deepcopy
from datetime import timedelta
from uuid import UUID, uuid4
import json
import threading
import time
import httpx
import pytest
from sqlalchemy import select, text
from app.modules.search import semantic_service as service, semantic_sources as sources, embedding_provider
from app.modules.search.semantic_models import SemanticIndex, SemanticChunk, SemanticQuery, SemanticCall
from app.modules.jobs import service as jobs
from app.modules.notes.models import Note, Tag
from app.shared.config import settings, Settings
from app.shared.db import SessionLocal, utcnow
from app.shared.models import Job, User
from app.shared.errors import AppError
from app.shared.security import hasher
from conftest import login, mutate
from test_keyword_search import create, recognize, correct
from test_image_chat import image_book




@pytest.fixture(autouse=True)
def private_embedding(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "private"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-ocr-tests-never-a-real-key")
    settings.cache_clear()
    def forbidden():
        raise AssertionError("No real embedding calls are permitted in tests")
    monkeypatch.setattr(outbound.httpx, "HTTPTransport", forbidden)
    monkeypatch.setattr(outbound, "resolve_public", lambda *a, **kw: ("8.8.8.8",))
    yield
    settings.cache_clear()


def install_model_transport(monkeypatch, handler):
    return _install_model_transport(monkeypatch, handler, kind="embedding")


def enable(monkeypatch, dimensions=64):
    monkeypatch.setenv("EMBEDDING_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_API_KEY", "fake-semantic-tests-no-real-secret")
    monkeypatch.setenv("EMBEDDING_WORKSPACE", "synthetic-tests")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", str(dimensions))
    settings.cache_clear()


def mock(monkeypatch, callback=None, invalid=None):
    calls = []
    def send(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if callback:
            callback()
        result = {"data": [{"index": i, "embedding": [1.0] + [0.0] * (payload["dimensions"] - 1)}
                           for i, _ in enumerate(payload["input"])], "usage": {"prompt_tokens": 3, "total_tokens": 3}}
        if invalid:
            invalid(result)
        return httpx.Response(200, json=result)
    install_model_transport(monkeypatch, send)
    return calls


def preview(client):
    response = client.get("/api/v1/semantic/preview")
    assert response.status_code == 200, response.text
    return response.json()


def build(client, monkeypatch, *, activate=True, run=True, request_key=None):
    manifest = preview(client)
    body = {"request_key": str(request_key or uuid4()), "manifest_hash": manifest["manifest_hash"], "confirm_external": True}
    response = mutate(client, "POST", "/semantic/indexes", body)
    assert response.status_code == 201, response.text
    index = response.json()
    if run:
        claimed = jobs.claim()
        assert claimed["kind"] == "semantic.build"
        assert service.process_job(claimed)
        index = next(row for row in client.get("/api/v1/semantic/indexes").json() if row["id"] == index["id"])
        assert index["status"] == "ready", index
        if activate:
            response = mutate(client, "POST", f"/semantic/indexes/{index['id']}/activate", {})
            assert response.status_code == 200, response.text
            index = response.json()
    return index, body


def query(client, **extra):
    return mutate(client, "POST", "/semantic/queries", {"request_key": str(uuid4()), "q": "相似主题", "confirm_external": True, **extra})


def test_default_off_auth_csrf_and_keyword_is_local(client):
    assert client.get("/api/v1/semantic/status").status_code == 401
    login(client)
    create(client, title="标题", plain_text="关键词")
    result = client.get("/api/v1/semantic/status").json()
    assert result["enabled"] is False and result["configured"] is False
    assert client.get("/api/v1/semantic/preview").status_code == 503
    assert client.get("/api/v1/search", params={"q": "关键词"}).status_code == 200
    assert client.post("/api/v1/semantic/queries", json={}).status_code == 403
    assert "fake-semantic" not in json.dumps(result)


def test_real_pgvector_and_snapshot_sources_chunk_boundaries(client, monkeypatch):
    enable(monkeypatch)
    login(client)
    tag = create(client, "tags", name="独立标签")
    create(client, title="独立标题", plain_text="汉" * 1601, tag_ids=[tag["id"]])
    result = preview(client)
    assert result["sources"] == {"title": 1, "tag": 1, "body": 1}
    assert result["source_count"] == 3 and result["chunk_count"] == 5
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        _, chunks = sources.manifest(db, user.id, service.configuration())
        bodies = [c for c in chunks if c["source"]["matched_field"] == "body"]
        assert [(c["start"], c["end"]) for c in bodies] == [(0, 800), (760, 1560), (1520, 1601)]
        assert db.scalar(text("SELECT '[1,0]'::vector <=> '[1,0]'::vector")) == 0


def test_build_query_source_dedupe_and_atomic_rollback(client, monkeypatch):
    enable(monkeypatch)
    calls = mock(monkeypatch)
    login(client)
    create(client, title="向量标题", plain_text="语义正文")
    index, body = build(client, monkeypatch)
    repeated = mutate(client, "POST", "/semantic/indexes", body)
    assert repeated.json()["id"] == index["id"] and len(calls) == 1
    assert query(client, confirm_external=False).status_code == 422
    key = str(uuid4())
    response = query(client, request_key=key)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "succeeded" and len(result["items"]) == 2
    assert result["items"][0]["score"] == 1
    assert query(client, request_key=key).json()["id"] == result["id"] and len(calls) == 2
    assert query(client, request_key=key, q="其他").status_code == 409
    hit = result["items"][0]
    source = client.get(f"/api/v1/semantic/chunks/{hit['chunk_id']}/source")
    assert source.status_code == 200 and source.json()["text"]
    second, _ = build(client, monkeypatch, activate=False)
    assert client.get("/api/v1/semantic/status").json()["active_index_id"] == index["id"]
    assert mutate(client, "POST", f"/semantic/indexes/{second['id']}/activate", {}).json()["status"] == "active"
    assert mutate(client, "POST", f"/semantic/indexes/{index['id']}/activate", {}).json()["status"] == "active"


def test_conflicting_manifest_and_one_build_per_user(client, monkeypatch):
    enable(monkeypatch)
    login(client)
    note = create(client, title="初始", plain_text="正文")
    original = preview(client)
    with SessionLocal.begin() as db:
        db.get(Note, UUID(note["id"])).title = "变化"
    bad = mutate(client, "POST", "/semantic/indexes", {"request_key": str(uuid4()), "manifest_hash": original["manifest_hash"], "confirm_external": True})
    assert bad.status_code == 409
    _, body = build(client, monkeypatch, run=False)
    assert mutate(client, "POST", "/semantic/indexes", {**body, "request_key": str(uuid4())}).status_code == 409
    assert mutate(client, "POST", "/semantic/indexes", {**body, "manifest_hash": "0" * 64}).status_code == 409


def test_dimension_switch_builds_new_version_query_uses_old_active_config(client, monkeypatch):
    enable(monkeypatch)
    calls = mock(monkeypatch)
    login(client)
    create(client, title="标题", plain_text="正文")
    first, _ = build(client, monkeypatch)
    enable(monkeypatch, 128)
    second, _ = build(client, monkeypatch, activate=False)
    result = query(client).json()
    assert result["status"] == "succeeded" and result["index_id"] == first["id"]
    assert [c["dimensions"] for c in calls] == [64, 128, 64]
    assert mutate(client, "POST", f"/semantic/indexes/{second['id']}/activate", {}).status_code == 200
    assert query(client).json()["index_id"] == second["id"]
    monkeypatch.setenv("EMBEDDING_WORKSPACE", "different-workspace")
    settings.cache_clear()
    assert query(client).status_code == 409
    assert len(calls) == 4


@pytest.mark.parametrize("change", ["before", "during", "config", "lease", "lease_during"])
def test_build_source_config_and_lease_guards_preserve_active(client, monkeypatch, change):
    enable(monkeypatch)
    calls = mock(monkeypatch)
    login(client)
    note = create(client, title="标题", plain_text="正文")
    first, _ = build(client, monkeypatch)
    second, _ = build(client, monkeypatch, run=False)
    claimed = jobs.claim()
    def modify():
        with SessionLocal.begin() as db:
            row = db.get(Note, UUID(note["id"]))
            row.plain_text, row.revision = "变化", row.revision + 1
    if change == "before":
        modify()
    elif change == "config":
        enable(monkeypatch, 128)
    elif change == "lease":
        with SessionLocal.begin() as db:
            db.get(Job, claimed["id"]).lease_until = utcnow() - timedelta(seconds=1)
    elif change == "lease_during":
        def expire():
            with SessionLocal.begin() as db:
                db.get(Job, claimed["id"]).lease_until = utcnow() - timedelta(seconds=1)
        mock(monkeypatch, callback=expire)
    else:
        mock(monkeypatch, callback=modify)
    assert not service.process_job(claimed)
    listing = client.get("/api/v1/semantic/indexes").json()
    failed = next(x for x in listing if x["id"] == second["id"])
    assert failed["status"] == "failed"
    assert client.get("/api/v1/semantic/status").json()["active_index_id"] == first["id"]
    if change in {"during", "lease_during"}:
        assert failed["usage"] == {"prompt_tokens": 3, "total_tokens": 3}
    assert mutate(client, "POST", f"/admin/jobs/{second['job_id']}/retry", {}).status_code == 409
    assert jobs.claim() is None


def test_stale_sources_removed_before_topk_and_source_recheck(client, monkeypatch):
    enable(monkeypatch)
    mock(monkeypatch)
    login(client)
    note = create(client, title="旧标题", plain_text="旧正文")
    other = create(client, title="可用标题", plain_text="可用正文")
    build(client, monkeypatch)
    result = query(client).json()
    old = next(h for h in result["items"] if h["note_id"] == note["id"])
    with SessionLocal.begin() as db:
        db.get(Note, UUID(note["id"])).deleted_at = utcnow()
    filtered = query(client, limit=1).json()
    assert len(filtered["items"]) == 1 and filtered["items"][0]["note_id"] == other["id"]
    assert client.get(f"/api/v1/semantic/chunks/{old['chunk_id']}/source").status_code == 409
    assert all(h["note_id"] != note["id"] for h in client.get("/api/v1/semantic/queries/" + result["id"]).json()["items"])


def test_query_postcall_source_changes_and_tag_rename(client, monkeypatch):
    enable(monkeypatch)
    mock(monkeypatch)
    login(client)
    tag = create(client, "tags", name="原标签")
    create(client, title="标题", plain_text="正文", tag_ids=[tag["id"]])
    build(client, monkeypatch)
    def rename():
        with SessionLocal.begin() as db:
            value = db.get(Tag, UUID(tag["id"]))
            value.name, value.revision = "新标签", value.revision + 1
    mock(monkeypatch, callback=rename)
    result = query(client).json()
    assert result["status"] == "succeeded" and all(h["matched_field"] != "tag" for h in result["items"])


def test_singleattempt_unknown_receipts_and_interrupted_queries(client, monkeypatch):
    enable(monkeypatch)
    calls = mock(monkeypatch)
    login(client)
    create(client, title="标题", plain_text="正文")
    index, _ = build(client, monkeypatch, run=False)
    claimed = jobs.claim()
    with SessionLocal.begin() as db:
        db.add(SemanticCall(index_id=UUID(index["id"]), batch=0, status="started"))
    assert not service.process_job(claimed) and calls == []
    build(client, monkeypatch)
    key = str(uuid4())
    def timeout(request):
        raise httpx.ReadTimeout("secret-and-source-must-not-leak")
    install_model_transport(monkeypatch, timeout)
    failed = query(client, request_key=key).json()
    assert failed["status"] == "failed" and "secret-and-source" not in json.dumps(failed)
    mock(monkeypatch)
    assert query(client, request_key=key).json()["status"] == "failed"
    with SessionLocal.begin() as db:
        row = db.get(SemanticQuery, UUID(failed["id"]))
        row.status, row.created_at = "running", utcnow() - timedelta(minutes=3)
    interrupted = client.get("/api/v1/semantic/queries/" + failed["id"]).json()
    assert interrupted["status"] == "failed" and interrupted["error_code"] == "semantic_interrupted"


@pytest.mark.parametrize("mutate_result", [
    lambda r: r["data"].clear(),
    lambda r: r["data"][0].update(index=True),
    lambda r: r["data"][0].update(index=3),
    lambda r: r["data"][0].update(embedding=[1.0]),
    lambda r: r["data"][0].update(embedding=[0.0] * 64),
    lambda r: r["data"][0].update(embedding=[True] * 64),
    lambda r: r["data"].append(deepcopy(r["data"][0])),
])
def test_malformed_response_is_terminal_and_usage_retained(client, monkeypatch, mutate_result):
    enable(monkeypatch)
    calls = mock(monkeypatch, invalid=mutate_result)
    login(client)
    create(client, title="标题")
    index, _ = build(client, monkeypatch, run=False)
    assert not service.process_job(jobs.claim())
    listing = client.get("/api/v1/semantic/indexes").json()[0]
    assert listing["status"] == "failed" and listing["usage"]["total_tokens"] == 3
    assert len(calls) == 1 and jobs.claim() is None


def test_ocr_latest_empty_correction_and_source_owner(client, monkeypatch):
    enable(monkeypatch)
    mock(monkeypatch)
    login(client)
    notebook = image_book(client)
    first = recognize(client, monkeypatch, notebook, text="旧识别")
    newest = recognize(client, monkeypatch, notebook, text="新识别")
    p = preview(client)
    assert p["sources"]["ocr"] == 1
    correct(client, newest, "")
    assert preview(client)["sources"].get("ocr", 0) == 0
    correct(client, {**newest, "revision": 2}, "有效识别")
    build(client, monkeypatch)
    result = query(client).json()
    hit = next(h for h in result["items"] if h["kind"] == "ocr")
    assert hit["id"] == newest["id"]
    with SessionLocal.begin() as db:
        db.add(User(username="outsider", display_name="其他", password_hash=hasher.hash("member-password")))
    login(client, "outsider", "member-password")
    assert client.get(f"/api/v1/semantic/chunks/{hit['chunk_id']}/source").status_code == 404
    assert client.get("/api/v1/semantic/queries/" + result["id"]).status_code == 404
    assert mutate(client, "POST", f"/semantic/indexes/{result['index_id']}/activate", {}).status_code == 404


def test_caps_and_server_private_configuration(client, monkeypatch):
    enable(monkeypatch)
    login(client)
    create(client, title="一", plain_text="文" * 64000)
    with SessionLocal.begin() as db:
        note = db.scalar(select(Note))
        note.plain_text = "字" * 400000
    assert client.get("/api/v1/semantic/preview").status_code == 413
    status = client.get("/api/v1/semantic/status")
    assert "fake-semantic-tests" not in status.text and "api_key" not in status.text
    for value in (63, 129, 4096):
        with pytest.raises(ValueError):
            Settings(_env_file=None, embedding_dimensions=value)
    with pytest.raises(ValueError):
        Settings(_env_file=None, embedding_workspace="evil.example/path")


def test_current_queries_ignore_unrelated_large_new_sources(client, monkeypatch):
    enable(monkeypatch)
    mock(monkeypatch)
    login(client)
    original = create(client, title="活动索引", plain_text="正文")
    build(client, monkeypatch)
    with SessionLocal.begin() as db:
        owner = db.scalar(select(User.id).where(User.username == "admin"))
        db.add(Note(owner_id=owner, title="新资料", plain_text="字" * 400001))
    assert client.get("/api/v1/semantic/preview").status_code == 413
    result = query(client).json()
    assert result["status"] == "succeeded" and result["items"]
    assert {h["note_id"] for h in result["items"]} == {original["id"]}
    assert client.get("/api/v1/semantic/chunks/" + result["items"][0]["chunk_id"] + "/source").status_code == 200


def test_filter_ownership_and_same_keyword_filter_semantics(client, monkeypatch):
    enable(monkeypatch)
    mock(monkeypatch)
    login(client)
    folder = create(client, "folders", name="目录")
    notebook = create(client, "notebooks", name="笔记本", folder_id=folder["id"])
    tag = create(client, "tags", name="标签")
    target = create(client, title="选中", plain_text="正文", notebook_id=notebook["id"], tag_ids=[tag["id"]])
    create(client, title="其他", plain_text="正文")
    build(client, monkeypatch)
    result = query(client, folder_id=folder["id"], notebook_id=notebook["id"], tag_id=tag["id"], kind="text").json()
    assert result["items"] and {h["note_id"] for h in result["items"]} == {target["id"]}
    assert query(client, tag_id=str(uuid4())).status_code == 404


def test_query_usage_survives_postcall_sql_failure(client, monkeypatch):
    enable(monkeypatch)
    mock(monkeypatch)
    login(client)
    create(client, title="标题", plain_text="正文")
    build(client, monkeypatch)
    def failed(*args, **kwargs):
        raise RuntimeError("private source and provider data")
    monkeypatch.setattr(sources, "current_chunks", failed)
    result = query(client).json()
    assert result["status"] == "failed" and result["usage"]["total_tokens"] == 3
    assert result["error_code"] == "semantic_query_failed" and "private source" not in json.dumps(result)


def test_query_owner_disabled_during_http_is_terminal_and_retains_known_usage(client, monkeypatch):
    enable(monkeypatch)
    mock(monkeypatch)
    with SessionLocal.begin() as db:
        member = User(username="query-member", display_name="查询账号", password_hash=hasher.hash("member-password"))
        db.add(member)
        db.flush()
        owner = member.id
    login(client, "query-member", "member-password")
    create(client, title="资料", plain_text="账号仍有效时建立的正文")
    build(client, monkeypatch)

    def disable_owner():
        # Separate transaction models the admin update while the original request
        # still holds CurrentUser in its expire_on_commit=False identity map.
        with SessionLocal.begin() as db:
            db.get(User, owner).status = "disabled"

    calls = mock(monkeypatch, callback=disable_owner)
    response = query(client)
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "failed" and result["error_code"] == "login_required"
    assert result["items"] == [] and result["usage"] == {"prompt_tokens": 3, "total_tokens": 3}
    assert len(calls) == 1
    with SessionLocal() as db:
        receipt = db.get(SemanticQuery, UUID(result["id"]))
        assert receipt.status == "failed" and receipt.hits == [] and receipt.usage["total_tokens"] == 3


@pytest.mark.parametrize("timing", ["before", "during"])
def test_build_owner_disabled_before_or_during_http_cannot_publish(client, monkeypatch, timing):
    enable(monkeypatch)
    with SessionLocal.begin() as db:
        member = User(username="build-member", display_name="构建账号", password_hash=hasher.hash("member-password"))
        db.add(member)
        db.flush()
        owner = member.id
    login(client, "build-member", "member-password")
    create(client, title="资料", plain_text="正文")
    index, _ = build(client, monkeypatch, run=False)
    claimed = jobs.claim()

    def disable_owner():
        with SessionLocal.begin() as db:
            db.get(User, owner).status = "disabled"

    calls = mock(monkeypatch, callback=disable_owner if timing == "during" else None)
    if timing == "before":
        disable_owner()
    assert not service.process_job(claimed)
    with SessionLocal() as db:
        failed = db.get(SemanticIndex, UUID(index["id"]))
        assert failed.status == "failed" and failed.error_code == "login_required"
        assert failed.completed_chunks == 0
        assert failed.usage == ({"prompt_tokens": 3, "total_tokens": 3} if timing == "during" else {})
        assert db.get(Job, claimed["id"]).status == "failed"
    assert len(calls) == (1 if timing == "during" else 0)


def test_build_user_lock_wait_expires_lease_before_any_paid_call(client, monkeypatch):
    enable(monkeypatch)
    calls = mock(monkeypatch)
    login(client)
    create(client, title="资料", plain_text="正文")
    old, _ = build(client, monkeypatch)
    candidate, _ = build(client, monkeypatch, run=False)
    calls.clear()
    claimed = jobs.claim()
    expiry = utcnow() + timedelta(seconds=0.5)
    with SessionLocal.begin() as db:
        job = db.get(Job, claimed["id"])
        owner = job.owner_id
        job.lease_until = expiry
    entering_owner_lock = threading.Event()
    original_owner = service._owner

    def observed_owner(db, identity):
        entering_owner_lock.set()
        return original_owner(db, identity)

    monkeypatch.setattr(service, "_owner", observed_owner)
    result = []
    worker = threading.Thread(target=lambda: result.append(service.process_job(claimed)), daemon=True)
    with SessionLocal.begin() as lock_db:
        lock_db.scalar(select(User).where(User.id == owner).with_for_update())
        worker.start()
        assert entering_owner_lock.wait(3)
        time.sleep(max(0, (expiry - utcnow()).total_seconds()) + 0.05)
    worker.join(timeout=5)
    assert not worker.is_alive() and result == [False]
    assert calls == []
    with SessionLocal() as db:
        assert db.get(SemanticIndex, UUID(candidate["id"])).status == "failed"
        assert db.get(SemanticIndex, UUID(old["id"])).status == "active"


def test_build_manifest_work_expiring_lease_prevents_paid_post(client, monkeypatch):
    enable(monkeypatch)
    calls = mock(monkeypatch)
    login(client)
    create(client, title="资料", plain_text="正文")
    old, _ = build(client, monkeypatch)
    candidate, _ = build(client, monkeypatch, run=False)
    calls.clear()
    claimed = jobs.claim()
    expiry = utcnow() + timedelta(seconds=0.2)
    with SessionLocal.begin() as db:
        db.get(Job, claimed["id"]).lease_until = expiry
    original_manifest = sources.manifest

    def slow_manifest(*args, **kwargs):
        result = original_manifest(*args, **kwargs)
        time.sleep(max(0, (expiry - utcnow()).total_seconds()) + 0.03)
        return result

    monkeypatch.setattr(sources, "manifest", slow_manifest)
    assert not service.process_job(claimed)
    assert calls == []
    with SessionLocal() as db:
        assert db.get(SemanticIndex, UUID(candidate["id"])).status == "failed"
        assert db.get(SemanticIndex, UUID(old["id"])).status == "active"


@pytest.mark.parametrize("payload", [b'null', b'[]', b'{broken', b'{"data":[{"index":0,"embedding":[NaN]}]}'])
def test_nonobject_invalid_json_provider_response_is_sanitized(monkeypatch, payload):
    enable(monkeypatch)
    install_model_transport(monkeypatch, lambda request: httpx.Response(200, content=payload))
    with pytest.raises(AppError) as error:
        embedding_provider.embed(["合成资料"], service.configuration())
    assert error.value.code == "embedding_response_invalid"


def test_bounded_response_and_tiny_vector_normalization(monkeypatch):
    enable(monkeypatch)
    install_model_transport(monkeypatch, lambda request: httpx.Response(200, content=b" " * (embedding_provider.MAX_RESPONSE_BYTES + 1)))
    with pytest.raises(AppError):
        embedding_provider.embed(["合成"], service.configuration())
    mock(monkeypatch, invalid=lambda r: r["data"][0].update(embedding=[1e-300] + [0.0] * 63))
    vectors, _ = embedding_provider.embed(["合成"], service.configuration())
    assert vectors == [[1.0] + [0.0] * 63]


def test_duplicate_indices_and_nonfinite_vectors_rejected(monkeypatch):
    enable(monkeypatch)
    mock(monkeypatch, invalid=lambda r: r["data"][1].update(index=0))
    with pytest.raises(AppError):
        embedding_provider.embed(["一", "二"], service.configuration())
    body = '{"data":[{"index":0,"embedding":[NaN,' + ','.join('0' for _ in range(63)) + ']}]}'
    install_model_transport(monkeypatch, lambda request: httpx.Response(200, content=body))
    with pytest.raises(AppError):
        embedding_provider.embed(["合成"], service.configuration())


def test_multibatch_snapshots_and_no_automatic_replay(client, monkeypatch):
    enable(monkeypatch)
    calls = mock(monkeypatch)
    login(client)
    create(client, title="多批资料", plain_text="字" * 8000)
    index, _ = build(client, monkeypatch)
    assert len(calls) == 2 and [len(c["input"]) for c in calls] == [10, 2]
    assert index["completed_chunks"] == 12 and index["usage"]["total_tokens"] == 6
    with SessionLocal() as db:
        receipts = db.scalars(select(SemanticCall).order_by(SemanticCall.batch)).all()
        assert [r.status for r in receipts] == ["succeeded", "succeeded"]
        chunks = db.scalars(select(SemanticChunk)).all()
        assert all("text" not in c.source for c in chunks)


def test_client_disables_redirects_proxies_and_retries(monkeypatch):
    enable(monkeypatch)
    captured = {}
    def factory(**kwargs):
        captured.update(kwargs)
        payload = {"data": [{"index": 0, "embedding": [1.0] + [0.0] * 63}], "usage": {}}
        return httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"},
            stream=httpx.ByteStream(json.dumps(payload).encode())))
    monkeypatch.setattr(outbound, "resolve_public", lambda *a, **kw: ("8.8.8.8",))
    monkeypatch.setattr(outbound.httpx, "HTTPTransport", factory)
    embedding_provider.embed(["合成"], service.configuration())
    assert captured == {"verify": True, "trust_env": False, "retries": 0}
