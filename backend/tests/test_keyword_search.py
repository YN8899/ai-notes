from app.shared import outbound
from model_transport import install_model_transport
"""T17: owner-scoped literal search and independently revalidated source access."""
from copy import deepcopy
from datetime import timedelta
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.modules.ai import chat_provider, chat_service
from app.modules.ai.models import AiRun, AiStep
from app.modules.files.models import File, FileVariant, NotePage
from app.modules.notes.models import Note
from app.modules.search.models import OcrDocument
from app.shared.config import settings
from app.shared.db import SessionLocal, utcnow
from app.shared.models import Job, User
from app.shared.security import hasher
from conftest import login, mutate
from test_image_chat import REGION, image_book, image_source, mock
from test_pdf_chat import pdf_book


@pytest.fixture(autouse=True)
def private_config(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-keyword-search-tests-never-a-real-key")
    settings.cache_clear()
    monkeypatch.setattr(settings(), "storage_root", str(tmp_path / "private"))

    def no_live():
        raise AssertionError("Keyword search tests must never call a live provider")

    monkeypatch.setattr(outbound.httpx, "HTTPTransport", no_live)
    monkeypatch.setattr(outbound, "resolve_public", lambda *a, **kw: ("8.8.8.8",))
    yield
    settings.cache_clear()


def create(client, resource="notes", **data):
    response = mutate(client, "POST", "/" + resource, data)
    assert response.status_code == 201, response.text
    return response.json()


def search(client, q, **params):
    response = client.get("/api/v1/search", params={"q": q, **params})
    assert response.status_code == 200, response.text
    page = response.json()
    assert set(page) == {"items", "next_cursor", "limit"}
    return page


def resolve(client, hit, q, **params):
    revisions = {"source_revision": hit["source_revision"], "match_hash": hit["match_hash"]}
    if hit.get("ocr_revision") is not None:
        revisions["ocr_revision"] = hit["ocr_revision"]
    return client.get(f"/api/v1/search/{hit['kind']}/{hit['id']}/source",
                      params={"q": q, **revisions, **params})


def recognize(client, monkeypatch, nid, text="识别关键词", *, region=None, pdf=False, page=0):
    if pdf:
        detail = client.get("/api/v1/ai/pdf-notebooks/" + nid).json()
        source = {"note_id": nid, "revision": detail["revision"],
                  "page_id": detail["pages"][page]["page_id"], "region": region}
    else:
        source = image_source(client, nid, page=page, region=region)
    response = mutate(client, "POST", "/ocr/documents", {**source, "request_key": str(uuid4())})
    assert response.status_code == 201, response.text
    row = response.json()
    mock(monkeypatch, answer=text)
    assert chat_service.tick()
    done = client.get("/api/v1/ocr/documents/" + row["id"])
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "succeeded", done.text
    return done.json()


def correct(client, row, text, reviewed=True):
    response = mutate(client, "PATCH", "/ocr/documents/" + row["id"],
                      {"revision": row["revision"], "corrected_text": text, "reviewed": reviewed})
    assert response.status_code == 200, response.text
    return response.json()


def ids(page):
    return {item["id"] for item in page["items"]}


def work_counts():
    with SessionLocal() as db:
        return tuple(db.scalar(select(func.count()).select_from(model)) for model in (AiRun, AiStep, Job))


def assert_match(row, text_field, query):
    text = row[text_field]
    assert 0 <= row["match_start"] < row["match_end"] <= len(text)
    assert text[row["match_start"]:row["match_end"]].translate(
        str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")) == query.translate(
        str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))


def test_note_fields_collapse_with_title_tag_body_priority(client):
    login(client)
    tag = create(client, "tags", name="重点关键词")
    title = create(client, title="关键词标题", plain_text="关键词正文", tag_ids=[tag["id"]])
    tagged = create(client, title="标签命中", plain_text="关键词正文", tag_ids=[tag["id"]])
    body = create(client, title="正文命中", plain_text="中文连续关键词内容")
    page = search(client, "关键词")
    assert page["limit"] == 20 and page["next_cursor"] is None
    assert {hit["id"]: hit["matched_field"] for hit in page["items"]} == {
        title["id"]: "title", tagged["id"]: "tag", body["id"]: "body"}
    for hit in page["items"]:
        assert hit["kind"] == "note" and hit["note_id"] == hit["id"]
        assert hit["source_revision"] == 1
        assert_match(hit, "snippet", "关键词")
        source = resolve(client, hit, "关键词")
        assert source.status_code == 200, source.text
        assert source.json()["matched_field"] == hit["matched_field"]
        assert_match(source.json(), "text", "关键词")


@pytest.mark.parametrize("text,query", [
    ("前缀中文连续字串后缀", "中文连续"), ("aBcDeF", "BcD"),
    ("🙂前面🧑‍💻后面", "🧑‍💻"), ("100%_\\literal", "%_\\"),
])
def test_literal_query_ascii_fold_and_codepoint_offsets(client, text, query):
    login(client)
    note = create(client, title="普通标题", plain_text=text)
    create(client, title="干扰项", plain_text="100anythingZliteral")
    page = search(client, "  " + query + "  ")
    assert ids(page) == {note["id"]}
    hit = page["items"][0]
    assert_match(hit, "snippet", query)
    source = resolve(client, hit, query)
    assert source.status_code == 200, source.text
    assert source.json()["text"] == text
    assert_match(source.json(), "text", query)


def test_non_ascii_case_is_literal_and_no_fuzzy_or_html_search(client):
    login(client)
    note = create(client, title="标题", plain_text="École <script>中文</script> café")
    assert ids(search(client, "École")) == {note["id"]}
    for query in ("école", "cafe", "中 文"):
        assert search(client, query)["items"] == []
    hit = search(client, "<script>")["items"][0]
    assert "<script>" in hit["snippet"]
    assert resolve(client, hit, "<script>").json()["text"] == note["plain_text"]


@pytest.mark.parametrize('new_name', ['新关键词', '不再命中'])
def test_tag_rename_invalidates_old_source_even_if_note_revision_unchanged(client, new_name):
    login(client)
    tag = create(client, 'tags', name='旧关键词')
    note = create(client, title='普通标题', plain_text='正文关键词', tag_ids=[tag['id']])
    hit = search(client, '关键词')['items'][0]
    assert hit['matched_field'] == 'tag'
    response = mutate(client, 'PATCH', '/tags/' + tag['id'], {'revision': 1, 'name': new_name})
    assert response.status_code == 200, response.text
    assert client.get('/api/v1/notes/' + note['id']).json()['revision'] == hit['source_revision']
    assert resolve(client, hit, '关键词').status_code == 409


@pytest.mark.parametrize("params", [{}, {"q": ""}, {"q": " \t "}, {"q": "字" * 101},
                                       {"q": "x", "limit": 0}, {"q": "x", "limit": 51},
                                       {"q": "x", "kind": "invalid"}, {"q": "x", "cursor": "broken"}])
def test_invalid_query_and_pagination_are_controlled(client, params):
    login(client)
    response = client.get("/api/v1/search", params=params)
    assert response.status_code == 422, response.text


def test_bounded_snippet_preserves_match_near_end_and_full_source(client):
    login(client)
    text = "🙂" * 260 + "定位关键词" + "尾" * 220
    create(client, title="长正文", plain_text=text)
    hit = search(client, "关键词")["items"][0]
    assert len(hit["snippet"]) <= 184
    assert_match(hit, "snippet", "关键词")
    source = resolve(client, hit, "关键词").json()
    assert source["text"] == text and source["match_start"] == 262


def test_ocr_raw_correction_and_explicit_empty_override(client, monkeypatch):
    login(client)
    row = recognize(client, monkeypatch, image_book(client, 1), "原始识别字")
    hit = search(client, "识别")["items"][0]
    assert hit["kind"] == "ocr" and hit["id"] == row["id"] and not hit["reviewed"]
    edited = correct(client, row, "人工纠正字")
    assert not search(client, "原始")["items"]
    hit = search(client, "纠正")["items"][0]
    assert hit["reviewed"] and hit["ocr_revision"] == 2
    assert resolve(client, hit, "纠正").json()["text"] == "人工纠正字"
    correct(client, edited, "")
    assert not search(client, "识别")["items"] and not search(client, "纠正")["items"]


def test_latest_success_selected_before_text_matching_and_by_scope(client, monkeypatch):
    login(client)
    nid = image_book(client, 2)
    old = recognize(client, monkeypatch, nid, "旧关键词")
    latest = recognize(client, monkeypatch, nid, "新的别字")
    region = recognize(client, monkeypatch, nid, "区域关键词", region=REGION)
    other_page = recognize(client, monkeypatch, nid, "另一页关键词", page=1)
    assert ids(search(client, "关键词")) == {region["id"], other_page["id"]}
    assert ids(search(client, "别字")) == {latest["id"]}
    correct(client, old, "修改旧稿关键词")
    assert old["id"] not in ids(search(client, "关键词"))


def test_equivalent_numeric_region_has_only_latest_success(client, monkeypatch):
    login(client)
    nid = image_book(client, 1)
    old = recognize(client, monkeypatch, nid, "旧稿关键词", region=REGION)
    with SessionLocal.begin() as db:
        run = db.get(AiRun, UUID(old['run_id']))
        source = deepcopy(run.source)
        # Both are valid numeric coordinates; JSON spelling must not make a new scope.
        source['pages'][0]['region']['y'] = 0
        source['pages'][0]['region']['height'] = 1
        run.source = source
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(run, 'source')
    latest = recognize(client, monkeypatch, nid, "当前识别", region=REGION)
    assert not search(client, '旧稿关键词')['items']
    assert ids(search(client, '当前识别')) == {latest['id']}


def test_explicit_null_pdf_kind_is_not_legacy_missing_kind(client, monkeypatch):
    login(client)
    row = recognize(client, monkeypatch, pdf_book(client, 1), 'PDF关键词', pdf=True)
    with SessionLocal.begin() as db:
        run = db.get(AiRun, UUID(row['run_id']))
        source = deepcopy(run.source)
        source['kind'] = None
        run.source = source
    assert not search(client, 'PDF关键词')['items']


@pytest.mark.parametrize("status", ["queued", "running", "failed", "interrupted"])
def test_unsuccessful_new_attempt_never_replaces_success(client, monkeypatch, status):
    login(client)
    nid = image_book(client, 1)
    old = recognize(client, monkeypatch, nid, "保留关键词")
    source = image_source(client, nid)
    response = mutate(client, "POST", "/ocr/documents", {**source, "request_key": str(uuid4())})
    assert response.status_code == 201, response.text
    with SessionLocal.begin() as db:
        run = db.get(AiRun, UUID(response.json()["run_id"]))
        run.status, run.answer = status, "伪造关键词"
    assert ids(search(client, "关键词")) == {old["id"]}


def test_invalid_new_success_does_not_hide_older_valid_success(client, monkeypatch):
    login(client)
    nid = image_book(client, 1)
    old = recognize(client, monkeypatch, nid, "有效关键词")
    newer = recognize(client, monkeypatch, nid, "新稿关键词")
    with SessionLocal.begin() as db:
        run = db.get(AiRun, UUID(newer["run_id"]))
        source = deepcopy(run.source)
        source["pages"][0]["bytes"] += 1
        run.source = source
    assert ids(search(client, "关键词")) == {old["id"]}


@pytest.mark.parametrize("change", ["pages_object", "empty_pages", "note_id", "file_id", "region", "mode"])
def test_malformed_frozen_ocr_source_is_excluded_without_leaking_or_crashing(client, monkeypatch, change):
    login(client)
    row = recognize(client, monkeypatch, image_book(client, 1), "历史关键词")
    hit = search(client, "关键词")["items"][0]
    with SessionLocal.begin() as db:
        run = db.get(AiRun, UUID(row["run_id"]))
        source = deepcopy(run.source)
        if change == "pages_object": source["pages"] = {"0": source["pages"][0]}
        elif change == "empty_pages": source["pages"] = []
        elif change == "note_id": source["note_id"] = str(uuid4())
        elif change == "file_id": source["pages"][0]["file_id"] = str(uuid4())
        elif change == "mode": source["mode"] = "region"
        else:
            source["mode"] = "region"
            source["pages"][0]["region"] = {**REGION, "x": True}
        run.source = source
    assert search(client, "关键词")["items"] == []
    response = resolve(client, hit, "关键词")
    assert response.status_code == 409, response.text
    assert "历史关键词" not in response.text


@pytest.mark.parametrize("change", ["hash", "dimensions", "file_status", "page", "variant_key", "variant_bytes", "trash"])
def test_invalid_sources_excluded_before_pagination(client, monkeypatch, change):
    login(client)
    valid_note = create(client, title="有效关键词")
    row = recognize(client, monkeypatch, image_book(client, 1), "无效关键词")
    with SessionLocal.begin() as db:
        file = db.get(File, UUID(row["file_id"]))
        if change == "hash": file.sha256 = "f" * 64
        elif change == "dimensions": file.width += 1
        elif change == "file_status": file.status = "expired"
        elif change == "page": db.delete(db.get(NotePage, UUID(row["page_id"])))
        elif change == "trash": db.get(Note, UUID(row["note_id"])).deleted_at = utcnow()
        else:
            variant = db.scalar(select(FileVariant).where(FileVariant.file_id == file.id, FileVariant.kind == "reading"))
            if change == "variant_key": variant.object_key += ".changed"
            else: variant.bytes += 1
    page = search(client, "关键词", limit=1)
    assert ids(page) == {valid_note["id"]} and page["next_cursor"] is None


def test_pdf_ocr_region_source_and_original_page_number(client, monkeypatch):
    login(client)
    row = recognize(client, monkeypatch, pdf_book(client, 2), "🙂PDF关键词", pdf=True, page=1, region=REGION)
    hit = search(client, "PDF关键词")["items"][0]
    assert hit["page_number"] == 2 and hit["region"] == REGION
    source = resolve(client, hit, "PDF关键词")
    assert source.status_code == 200, source.text
    data = source.json()
    assert data["file_id"] == row["file_id"] and data["page_id"] == row["page_id"]
    assert data["id"] == row["id"] and data["page_number"] == 2 and data["region"] == REGION
    assert data["match_start"] == 1
    with SessionLocal.begin() as db:
        db.get(NotePage, UUID(row["page_id"])).pdf_page_number = 3
    assert not search(client, "PDF关键词")["items"]


def test_retired_image_active_count_limit_preserves_ocr_citation_201_in_keyword_and_semantic_sources(client, monkeypatch):
    from app.modules.search import semantic_sources
    from test_offline_sync import operation, sync
    login(client)
    note_id = image_book(client, count=201)
    physical = client.get(f"/api/v1/notes/{note_id}/pages").json()
    sync(client, operation("page.remove", physical[0]["id"], {"note_id": note_id}, 2))
    detail = client.get(f"/api/v1/ai/image-notebooks/{note_id}")
    assert detail.status_code == 200 and detail.json()["page_count"] == 200
    assert detail.json()["pages"][-1]["number"] == 201
    control = recognize(client, monkeypatch, note_id, "可检索对照200", page=198)
    candidate = recognize(client, monkeypatch, note_id, "可检索图片201", page=199)
    assert (control["page_number"], candidate["page_number"]) == (200, 201)
    assert control["source_valid"] and candidate["source_valid"]
    assert control["id"] in ids(search(client, "可检索对照200"))
    found = search(client, "可检索图片201")
    assert candidate["id"] in ids(found)
    hit = next(hit for hit in found["items"] if hit["id"] == candidate["id"])
    assert hit["page_number"] == 201
    assert resolve(client, hit, "可检索图片201").status_code == 200
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.username == "admin"))
        source_keys = semantic_sources.sources(db, owner.id)
    assert f"ocr:{control['id']}" in source_keys
    assert f"ocr:{candidate['id']}" in source_keys


def test_pdf_citation_201_remains_excluded_from_keyword_and_semantic_sources(client, monkeypatch):
    from app.modules.files.models import PdfImport
    from app.modules.search import semantic_sources
    login(client)
    note_id = pdf_book(client)
    row = recognize(client, monkeypatch, note_id, "非法原PDF页201", pdf=True)
    assert row["id"] in ids(search(client, "非法原PDF页201"))
    with SessionLocal.begin() as db:
        run = db.get(AiRun, UUID(row["run_id"]))
        run.source = {**run.source, "pages": [{**run.source["pages"][0], "number": 201}]}
        db.get(NotePage, UUID(row["page_id"])).pdf_page_number = 201
        item = db.scalar(select(PdfImport).where(PdfImport.note_id == UUID(note_id)))
        item.page_count = item.completed_pages = 201
    assert search(client, "非法原PDF页201")["items"] == []
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.username == "admin"))
        assert f"ocr:{row['id']}" not in semantic_sources.sources(db, owner.id)


@pytest.mark.parametrize("number", [0, -1, True, "201", 201.5, 2147483648, 10**30])
def test_malformed_or_overflow_image_citation_still_fails_closed(client, monkeypatch, number):
    from app.modules.search import semantic_sources
    from sqlalchemy.orm.attributes import flag_modified
    login(client)
    row = recognize(client, monkeypatch, image_book(client, 1), "无效图片页标签")
    with SessionLocal.begin() as db:
        run = db.get(AiRun, UUID(row["run_id"]))
        run.source = {**run.source, "pages": [{**run.source["pages"][0], "number": number}]}
        # JSONB equality tracking otherwise treats Python True == 1 as no
        # change, leaving the original valid label in the malformed fixture.
        flag_modified(run, "source")
    with SessionLocal() as db:
        persisted = db.get(AiRun, UUID(row["run_id"])).source["pages"][0]["number"]
        assert type(persisted) is type(number) and persisted == number
    assert search(client, "无效图片页标签")["items"] == []
    with SessionLocal() as db:
        owner = db.scalar(select(User).where(User.username == "admin"))
        assert f"ocr:{row['id']}" not in semantic_sources.sources(db, owner.id)


def test_filters_combine_and_folder_means_direct_notebooks(client, monkeypatch):
    login(client)
    folder = create(client, "folders", name="课程")
    child = create(client, "folders", name="子目录", parent_id=folder["id"])
    book = create(client, "notebooks", name="数学", folder_id=folder["id"])
    sub_book = create(client, "notebooks", name="子目录本", folder_id=child["id"])
    tag = create(client, "tags", name="筛选标签")
    text = create(client, title="共同关键词", notebook_id=book["id"], tag_ids=[tag["id"]])
    create(client, title="共同关键词子目录", notebook_id=sub_book["id"])
    image = image_book(client, 1)
    current = client.get("/api/v1/notes/" + image).json()
    updated = mutate(client, "PATCH", "/notes/" + image,
                     {"revision": current["revision"], "notebook_id": book["id"], "tag_ids": [tag["id"]]})
    assert updated.status_code == 200, updated.text
    ocr = recognize(client, monkeypatch, image, "共同关键词")
    common = {"folder_id": folder["id"], "notebook_id": book["id"], "tag_id": tag["id"]}
    assert ids(search(client, "共同关键词", **common)) == {text["id"], ocr["id"]}
    assert ids(search(client, "共同关键词", **common, kind="text")) == {text["id"]}
    assert ids(search(client, "共同关键词", **common, kind="image")) == {ocr["id"]}
    assert search(client, "共同关键词", folder_id=child["id"], notebook_id=book["id"])["items"] == []


def test_admin_cannot_search_or_resolve_another_owners_private_data(client, monkeypatch):
    login(client)
    private = create(client, title="隐私关键词")
    ocr = recognize(client, monkeypatch, image_book(client, 1), "隐私关键词")
    hits = search(client, "隐私关键词")["items"]
    folder = create(client, "folders", name="私有目录")
    book = create(client, "notebooks", name="私有本", folder_id=folder["id"])
    tag = create(client, "tags", name="私有标签")
    with SessionLocal.begin() as db:
        db.add(User(username="other_admin", display_name="其他管理员", role="admin", password_hash=hasher.hash("other-admin-password")))
    login(client, "other_admin", "other-admin-password")
    page = search(client, "隐私关键词")
    assert page == {"items": [], "next_cursor": None, "limit": 20}
    assert private["id"] not in json.dumps(page) and ocr["id"] not in json.dumps(page)
    for hit in hits:
        assert resolve(client, hit, "隐私关键词").status_code == 404
    for field, resource in (("folder_id", folder), ("notebook_id", book), ("tag_id", tag)):
        response = client.get("/api/v1/search", params={"q": "隐私关键词", field: resource["id"]})
        absent = client.get("/api/v1/search", params={"q": "隐私关键词", field: str(uuid4())})
        assert response.status_code == absent.status_code == 404
        assert response.json()["code"] == absent.json()["code"]


def test_ordinary_owner_cannot_search_admin_data_and_login_is_required(client):
    assert client.get("/api/v1/search", params={"q": "关键词"}).status_code == 401
    login(client)
    note = create(client, title="管理员关键词")
    hit = search(client, "关键词")["items"][0]
    with SessionLocal.begin() as db:
        db.add(User(username="member", display_name="普通用户", password_hash=hasher.hash("member-password")))
    login(client, "member", "member-password")
    assert not search(client, "关键词")["items"]
    assert resolve(client, hit, "关键词").status_code == 404
    assert note["id"] == hit["id"]


def test_same_timestamp_keyset_pagination_is_stable_and_has_no_duplicates(client, monkeypatch):
    login(client)
    notes = [create(client, title=f"分页关键词{i}") for i in range(3)]
    ocr = recognize(client, monkeypatch, image_book(client, 1), "分页关键词")
    stamp = utcnow() - timedelta(seconds=1)
    with SessionLocal.begin() as db:
        for row in notes: db.get(Note, UUID(row["id"])).updated_at = stamp
        db.get(OcrDocument, UUID(ocr["id"])).updated_at = stamp
        db.get(AiRun, UUID(ocr["run_id"])).finished_at = stamp
    all_items = search(client, "分页关键词", limit=50)["items"]
    expected = sorted([(item["kind"], item["id"]) for item in all_items], reverse=True)
    assert [(item["kind"], item["id"]) for item in all_items] == expected
    seen, cursor = [], None
    for _ in range(5):
        page = search(client, "分页关键词", limit=1, **({"cursor": cursor} if cursor else {}))
        seen.extend((item["kind"], item["id"]) for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None: break
    assert seen == expected and len(seen) == 4


def test_cursor_binds_query_each_filter_and_owner(client):
    login(client)
    folder = create(client, "folders", name="游标目录")
    book = create(client, "notebooks", name="游标本", folder_id=folder["id"])
    tag = create(client, "tags", name="游标标签")
    for i in range(2): create(client, title=f"游标关键词{i}", notebook_id=book["id"], tag_ids=[tag["id"]])
    cursor = search(client, "游标关键词", limit=1)["next_cursor"]
    assert cursor
    for changes in ({"q": "别的词"}, {"kind": "text"}, {"folder_id": folder["id"]},
                    {"notebook_id": book["id"]}, {"tag_id": tag["id"]}):
        response = client.get("/api/v1/search", params={"q": "游标关键词", "cursor": cursor, **changes})
        assert response.status_code == 422, response.text
    with SessionLocal.begin() as db:
        db.add(User(username="member", display_name="成员", password_hash=hasher.hash("member-password")))
    login(client, "member", "member-password")
    assert client.get("/api/v1/search", params={"q": "游标关键词", "cursor": cursor}).status_code == 422


def test_cursor_cutoff_excludes_later_insert_and_update(client):
    login(client)
    notes = [create(client, title=f"截止关键词{i}") for i in range(3)]
    page = search(client, "截止关键词", limit=1)
    shown = page["items"][0]["id"]
    later = create(client, title="截止关键词后来插入")
    changed = next(note for note in notes if note["id"] != shown)
    assert mutate(client, "PATCH", "/notes/" + changed["id"], {"revision": 1, "title": "截止关键词更新"}).status_code == 200
    rest = search(client, "截止关键词", limit=50, cursor=page["next_cursor"])
    assert later["id"] not in ids(rest) and changed["id"] not in ids(rest) and shown not in ids(rest)


def test_note_source_rechecks_revision_match_and_trash(client):
    login(client)
    note = create(client, title="源标题", plain_text="🙂源码关键词")
    hit = search(client, "关键词")["items"][0]
    source = resolve(client, hit, "关键词")
    assert source.status_code == 200 and source.json()["match_start"] == 3
    assert resolve(client, hit, "不存在").status_code == 409
    assert mutate(client, "PATCH", "/notes/" + note["id"], {"revision": 1, "plain_text": "新关键词"}).status_code == 200
    assert resolve(client, hit, "关键词").status_code == 409
    assert mutate(client, "DELETE", "/notes/" + note["id"], {"revision": 2}).status_code == 204
    assert resolve(client, hit, "关键词").status_code == 404
    assert not search(client, "关键词")["items"]


def test_ocr_source_rechecks_correction_and_latest_winner(client, monkeypatch):
    login(client)
    nid = image_book(client, 1)
    row = recognize(client, monkeypatch, nid, "旧关键词")
    hit = search(client, "关键词")["items"][0]
    correct(client, row, "修改关键词")
    assert resolve(client, hit, "关键词").status_code == 409
    hit = search(client, "关键词")["items"][0]
    recognize(client, monkeypatch, nid, "全新关键词")
    assert resolve(client, hit, "关键词").status_code == 409


def test_ocr_source_rechecks_current_file_identity(client, monkeypatch):
    login(client)
    row = recognize(client, monkeypatch, image_book(client, 1), "来源关键词")
    hit = search(client, "关键词")["items"][0]
    with SessionLocal.begin() as db: db.get(File, UUID(row["file_id"])).sha256 = "e" * 64
    response = resolve(client, hit, "关键词")
    assert response.status_code == 409, response.text
    assert "来源关键词" not in response.text


def test_search_and_source_need_no_model_config_and_create_no_work(client, monkeypatch):
    login(client)
    create(client, title="文本关键词")
    recognize(client, monkeypatch, image_book(client, 1), "OCR关键词", region=REGION)
    monkeypatch.setattr(settings(), "deepseek_api_key", type(settings().deepseek_api_key)(""))
    monkeypatch.setattr(outbound.httpx, "HTTPTransport", lambda: pytest.fail("Search must not create a provider"))
    monkeypatch.setattr(outbound, "resolve_public", lambda *a, **kw: ("8.8.8.8",))
    before = work_counts()
    page = search(client, "关键词")
    assert len(page["items"]) == 2
    for hit in page["items"]:
        response = resolve(client, hit, "关键词")
        assert response.status_code == 200, response.text
        assert "object_key" not in response.text and "fake-keyword" not in response.text
    assert work_counts() == before
    encoded = json.dumps(page)
    for forbidden in ("object_key", "input_messages", "request_hash", "password", "total"):
        assert forbidden not in encoded
