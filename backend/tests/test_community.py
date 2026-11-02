"""Community HTTP, independent file copies, lease and moderation invariants."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.modules.community import publishing
from app.modules.community.models import CommunityAsset, CommunityComment, CommunityPost, CommunityReaction, CommunityReport, CommunitySnapshot
from app.modules.files.models import File
from app.modules.files.service import storage
from app.modules.jobs import service as jobs
from app.shared.db import SessionLocal, utcnow
from app.shared.models import Audit, Job, User
from conftest import login, mutate
from test_social import people, create_note, image_note


def create(client, **kwargs):
    response = mutate(client, "POST", "/community/posts", {"title": "学习分享", "plain_text": "原创学习总结", "subject": "数学", "post_type": "note", "tags": ["学习"], **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def draft(client, post):
    response = client.get(f"/api/v1/community/posts/{post['id']}/draft")
    assert response.status_code == 200, response.text
    return response.json()


def submit(client, post, key=None):
    current = draft(client, post)
    payload = {"revision": current["revision"], "request_key": str(key or uuid4())}
    response = mutate(client, "POST", f"/community/posts/{post['id']}/publish", payload)
    assert response.status_code == 202, response.text
    return response.json(), payload


def run_one():
    claimed = jobs.claim()
    assert claimed and claimed["kind"] == "community.publish"
    assert publishing.process_job(claimed)
    return claimed


def published(client, **kwargs):
    post = create(client, **kwargs)
    submit(client, post)
    run_one()
    return post


def patch(client, post, **changes):
    current = draft(client, post)
    fields = {key: current[key] for key in ("title", "body_json", "plain_text", "subject", "post_type", "tags", "source_note_id", "source_revision", "revision")}
    return mutate(client, "PATCH", f"/community/posts/{post['id']}", {**fields, **changes})


def source_image(client, owner_id):
    note, page_id, fid, content = image_note(client, owner_id)
    with SessionLocal() as db:
        file = db.get(File, fid)
        storage().put(file.object_key, content)
    return note, content


def test_original_post_requires_explicit_publish_and_drafts_are_private(people, client):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    post = create(a)
    login(client)
    for reader in (a, b, client):
        assert reader.get("/api/v1/community/posts/" + post["id"]).status_code == 404
    for stranger in (b, client):
        assert stranger.get(f"/api/v1/community/posts/{post['id']}/draft").status_code == 404
        assert mutate(stranger, "PATCH", f"/community/posts/{post['id']}", {"revision": 1, "title": "越权编辑"}).status_code == 404
    assert b.get("/api/v1/community/posts").json()["items"] == []
    result, payload = submit(a, post)
    assert result["status"] == "preparing"
    assert b.get("/api/v1/community/posts").json()["items"] == []
    again = mutate(a, "POST", f"/community/posts/{post['id']}/publish", payload)
    assert again.status_code == 202 and again.json() == result
    run_one()
    public = b.get("/api/v1/community/posts/" + post["id"])
    assert public.status_code == 200 and public.json()["plain_text"] == "原创学习总结"
    assert public.json()["snapshot_version"] == 1
    assert public.json()["author"]["id"] == ids["alice"]
    assert not any(k in public.json() for k in ("owner_id", "source_note_id", "request_key", "job_id", "moderation_reason"))


def test_edits_are_private_until_another_explicit_publish_and_old_receipt_replays(people):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    post = create(a)
    result, payload = submit(a, post)
    run_one()
    assert patch(a, post, title="新的私密草稿", plain_text="修改的草稿").status_code == 200
    assert b.get("/api/v1/community/posts/" + post["id"]).json()["title"] == "学习分享"
    replay = mutate(a, "POST", f"/community/posts/{post['id']}/publish", payload)
    assert replay.status_code == 202 and replay.json()["snapshot_id"] == result["snapshot_id"]
    submit(a, post)
    assert b.get("/api/v1/community/posts/" + post["id"]).json()["title"] == "学习分享"
    run_one()
    live = b.get("/api/v1/community/posts/" + post["id"]).json()
    assert live["title"] == "新的私密草稿" and live["snapshot_version"] == 2
    with SessionLocal() as db:
        history = db.scalars(select(CommunitySnapshot).order_by(CommunitySnapshot.version)).all()
        assert [row.title for row in history] == ["学习分享", "新的私密草稿"]


def test_private_source_body_links_are_sanitized_without_arbitrary_metadata(people):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    body = {"type": "doc", "private_object_key": "secret/private.jpg", "content": [
        {"type": "image", "attrs": {"src": "/api/v1/files/private/content?ticket=secret"}},
        {"type": "paragraph", "attrs": {"note_id": str(uuid4())}, "content": [
            {"type": "text", "text": "保留文字", "marks": [{"type": "link", "attrs": {"href": "/api/v1/notes/private"}}, {"type": "bold", "private": "secret"}]},
            {"type": "text", "text": "外链", "marks": [{"type": "link", "attrs": {"href": "https://example.org/study", "private": "secret"}}]}]},
        {"type": "inlineMath", "attrs": {"latex": "x^2", "private": "secret"}},
    ]}
    post = published(a, body_json=body)
    output = b.get("/api/v1/community/posts/" + post["id"]).json()
    encoded = json.dumps(output["body_json"])
    assert "private" not in encoded and "secret" not in encoded and "/api/" not in encoded
    assert "https://example.org/study" in encoded
    assert "inlineMath" in encoded and "x^2" in encoded


def test_source_note_requires_owner_current_revision_and_no_shared_grant_shortcut(people):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    note = create_note(a)
    body = {"title": "快照", "source_note_id": note["id"], "source_revision": 1}
    assert mutate(b, "POST", "/community/posts", body).status_code == 404
    assert mutate(a, "POST", "/community/posts", {**body, "source_revision": 2}).status_code == 409
    post = create(a, source_note_id=note["id"], source_revision=1, plain_text="")
    assert draft(a, post)["plain_text"] == note["plain_text"]
    assert mutate(a, "PATCH", "/notes/" + note["id"], {"revision": 1, "plain_text": "来源变化"}).status_code == 200
    current = draft(a, post)
    assert mutate(a, "POST", f"/community/posts/{post['id']}/publish", {"revision": current["revision"], "request_key": str(uuid4())}).status_code == 409
    assert b.get("/api/v1/community/posts").json()["items"] == []


def test_independent_image_originals_survive_source_changes_deletion_and_private_object_removal(people):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    note, content = source_image(a, ids["alice"])
    post = published(a, source_note_id=note["id"], source_revision=1, plain_text="")
    output = b.get("/api/v1/community/posts/" + post["id"]).json()
    assert len(output["assets"]) == 1
    asset = output["assets"][0]
    assert "source_note_id" not in json.dumps(output) and "object_key" not in json.dumps(output)
    assert b.get(asset["image_url"]).content == content
    assert b.get(asset["original_url"]).content == content
    with SessionLocal() as db:
        clone = db.scalar(select(CommunityAsset))
        assert clone.status == "ready" and clone.source_object_key is None and clone.source_original_object_key is None
        assert clone.object_key.startswith("community/") and clone.original_object_key.startswith("community/")
        source = db.scalar(select(File).where(File.owner_id == UUID(ids["alice"])))
        storage().delete(source.object_key)
        # The source helper's reading path is distinct from the original suffix.
        storage().delete(source.object_key.removesuffix(".original"))
    assert mutate(a, "DELETE", "/notes/" + note["id"], {"revision": 1}).status_code == 204
    assert b.get(asset["image_url"]).status_code == 200
    assert b.get(asset["original_url"]).content == content
    assert b.get("/api/v1/community/posts/" + post["id"]).status_code == 200


def test_partial_copy_failure_never_publishes_and_manual_retry_reuses_verified_copies(people, monkeypatch):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    note, _ = source_image(a, ids["alice"])
    post = create(a, source_note_id=note["id"], source_revision=1, plain_text="")
    result, _ = submit(a, post)
    with SessionLocal.begin() as db:
        db.get(Job, UUID(result["job_id"])).max_attempts = 1
    claimed = jobs.claim()
    adapter = storage()
    real_put = adapter.put

    def fail_original(key, data):
        if key.startswith("community/") and key.endswith("/original"):
            raise OSError("synthetic local failure")
        real_put(key, data)

    monkeypatch.setattr(adapter, "put", fail_original)
    monkeypatch.setattr(publishing, "storage", lambda: adapter)
    with pytest.raises(OSError):
        publishing.process_job(claimed)
    jobs.fail(claimed["id"], claimed["token"])
    assert b.get("/api/v1/community/posts/" + post["id"]).status_code == 404
    assert b.get("/api/v1/community/posts").json()["items"] == []
    current = draft(a, post)
    assert current["publish_status"] == "failed"
    monkeypatch.setattr(adapter, "put", real_put)
    retry = mutate(a, "POST", f"/community/posts/{post['id']}/retry", {"revision": current["revision"]})
    assert retry.status_code == 202, retry.text
    run_one()
    assert b.get("/api/v1/community/posts/" + post["id"]).status_code == 200


def test_old_worker_token_cannot_publish_or_change_asset_state(people):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    post = create(a)
    result, _ = submit(a, post)
    old = jobs.claim()
    with SessionLocal.begin() as db:
        db.get(Job, old["id"]).lease_until = utcnow() - timedelta(seconds=1)
    current = jobs.claim()
    assert current["token"] != old["token"]
    assert publishing.process_job(old) is False
    assert b.get("/api/v1/community/posts/" + post["id"]).status_code == 404
    assert publishing.process_job(current) is True
    assert publishing.process_job(old) is False


def test_withdraw_cancels_inflight_publication_and_republish_is_explicit(people):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    post = create(a)
    submit(a, post)
    claimed = jobs.claim()
    current = draft(a, post)
    withdrawn = mutate(a, "POST", f"/community/posts/{post['id']}/withdraw", {"revision": current["revision"]})
    assert withdrawn.status_code == 200 and withdrawn.json()["status"] == "withdrawn"
    assert publishing.process_job(claimed) is False
    assert b.get("/api/v1/community/posts").json()["items"] == []
    submit(a, post)
    run_one()
    assert b.get("/api/v1/community/posts/" + post["id"]).status_code == 200


def test_withdrawn_content_blocks_all_new_public_reads_and_interactions(people):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    note, _ = source_image(a, ids["alice"])
    post = published(a, source_note_id=note["id"], source_revision=1, plain_text="")
    live = b.get("/api/v1/community/posts/" + post["id"]).json()
    mutate(b, "PUT", f"/community/posts/{post['id']}/bookmark", {})
    assert mutate(a, "POST", f"/community/posts/{post['id']}/withdraw", {"revision": draft(a, post)["revision"]}).status_code == 200
    assert b.get(live["assets"][0]["image_url"]).status_code == 404
    assert b.get(live["assets"][0]["original_url"]).status_code == 404
    assert b.get("/api/v1/community/bookmarks").json()["items"] == []
    for action in ("like", "bookmark"):
        assert mutate(b, "PUT", f"/community/posts/{post['id']}/{action}", {}).status_code == 404
    assert mutate(b, "POST", f"/community/posts/{post['id']}/comments", {"body": "不能评论", "request_key": str(uuid4())}).status_code == 404
    assert mutate(b, "POST", f"/community/posts/{post['id']}/reports", {"reason": "不能读", "request_key": str(uuid4())}).status_code == 404


def test_asset_from_another_post_and_corrupted_copy_are_rejected(people):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    note, _ = source_image(a, ids["alice"])
    first = published(a, source_note_id=note["id"], source_revision=1, plain_text="")
    second = published(a, source_note_id=note["id"], source_revision=1, plain_text="")
    asset = b.get("/api/v1/community/posts/" + first["id"]).json()["assets"][0]
    assert b.get(asset["image_url"].replace(first["id"], second["id"])).status_code == 404
    with SessionLocal() as db:
        row = db.get(CommunityAsset, UUID(asset["id"]))
        data = storage().read(row.object_key)
        storage().put(row.object_key, b"x" * len(data))
    assert b.get(asset["image_url"]).status_code == 503


def test_frozen_source_hash_rejects_same_length_source_corruption(people):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    note, _ = source_image(a, ids["alice"])
    post = create(a, source_note_id=note["id"], source_revision=1, plain_text="")
    submit(a, post)
    claimed = jobs.claim()
    with SessionLocal() as db:
        asset = db.scalar(select(CommunityAsset))
        storage().put(asset.source_object_key, b"x" * asset.bytes)
    with pytest.raises(ValueError, match="source_changed"):
        publishing.process_job(claimed)
    jobs.fail(claimed["id"], claimed["token"])
    assert b.get("/api/v1/community/posts/" + post["id"]).status_code == 404


def test_publication_request_key_conflicts_and_busy_editor_conflicts(people):
    clients, ids = people
    a = clients["alice"]
    first, second = create(a), create(a)
    result, payload = submit(a, first)
    assert patch(a, first, title="在途覆盖").status_code == 409
    current = draft(a, first)
    assert mutate(a, "POST", f"/community/posts/{first['id']}/publish", {"revision": current["revision"], "request_key": str(uuid4())}).status_code == 409
    second_payload = {"revision": second["revision"], "request_key": payload["request_key"]}
    assert mutate(a, "POST", f"/community/posts/{second['id']}/publish", second_payload).status_code == 409
    assert mutate(a, "POST", f"/community/posts/{first['id']}/publish", {**payload, "revision": 999}).status_code == 409


def test_feed_filters_following_recommendation_and_viewer_scope_cursor(people):
    clients, ids = people
    a, b, c = clients["alice"], clients["bob"], clients["charlie"]
    first = published(a, title="长文", plain_text="数学" * 150, post_type="resource", tags=["公式", "学习"])
    second = published(c, title="物理问题", subject="物理", post_type="question", tags=["力学"])
    third = published(a, title="数学经验", post_type="experience", tags=["学习"])
    path = "/api/v1/community/posts"
    assert {row["id"] for row in b.get(path).json()["items"]} == {first["id"], second["id"], third["id"]}
    assert [row["id"] for row in b.get(path, params={"subject": "物理", "post_type": "question", "tag": "力学"}).json()["items"]] == [second["id"]]
    assert b.get(path, params={"mode": "following"}).json()["items"] == []
    mutate(b, "PUT", "/social/follows/" + ids["alice"], {})
    assert {row["id"] for row in b.get(path, params={"mode": "following"}).json()["items"]} == {first["id"], third["id"]}
    recommended = b.get(path, params={"mode": "recommended", "limit": 1}).json()
    assert recommended["items"][0]["id"] == first["id"]
    assert "200" in recommended["items"][0]["recommendation_reason"]
    cursor = recommended["next_cursor"]
    assert cursor
    next_page = b.get(path, params={"mode": "recommended", "cursor": cursor, "limit": 1})
    assert next_page.status_code == 200 and next_page.json()["items"][0]["id"] != first["id"]
    assert a.get(path, params={"mode": "recommended", "cursor": cursor}).status_code == 422
    assert b.get(path, params={"cursor": cursor}).status_code == 422
    assert b.get(path, params={"mode": "recommended", "subject": "物理", "cursor": cursor}).status_code == 422


def test_likes_bookmarks_comments_and_request_idempotency(people):
    clients, ids = people
    a, b, c = clients["alice"], clients["bob"], clients["charlie"]
    post = published(a)
    path = "/community/posts/" + post["id"]
    for _ in range(2):
        assert mutate(b, "PUT", path + "/like", {}).status_code == 204
        assert mutate(b, "PUT", path + "/bookmark", {}).status_code == 204
    payload = {"body": "评论内容", "request_key": str(uuid4())}
    comment = mutate(b, "POST", path + "/comments", payload)
    assert comment.status_code == 201
    assert mutate(b, "POST", path + "/comments", payload).json()["id"] == comment.json()["id"]
    assert mutate(b, "POST", path + "/comments", {**payload, "body": "不同内容"}).status_code == 409
    output = b.get("/api/v1" + path).json()
    assert output["like_count"] == 1 and output["comment_count"] == 1 and output["liked"] and output["bookmarked"]
    assert b.get("/api/v1/community/bookmarks").json()["items"][0]["id"] == post["id"]
    assert c.get("/api/v1/community/bookmarks").json()["items"] == []
    cid = comment.json()["id"]
    assert mutate(a, "DELETE", "/community/comments/" + cid, {}).status_code == 404
    assert mutate(c, "DELETE", "/community/comments/" + cid, {}).status_code == 404
    assert mutate(b, "DELETE", "/community/comments/" + cid, {}).status_code == 204
    assert b.get("/api/v1" + path + "/comments").json()["items"] == []
    assert mutate(b, "DELETE", path + "/like", {}).status_code == 204
    assert mutate(b, "DELETE", path + "/bookmark", {}).status_code == 204
    assert b.get("/api/v1" + path).json()["like_count"] == 0


def test_report_moderation_hides_all_public_paths_and_audits_without_private_access(people, client):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    note, _ = source_image(a, ids["alice"])
    post = published(a, source_note_id=note["id"], source_revision=1, plain_text="")
    public = b.get("/api/v1/community/posts/" + post["id"]).json()
    asset_url = public["assets"][0]["image_url"]
    mutate(b, "PUT", f"/community/posts/{post['id']}/bookmark", {})
    payload = {"reason": "不恰当内容", "request_key": str(uuid4())}
    report = mutate(b, "POST", f"/community/posts/{post['id']}/reports", payload)
    assert report.status_code == 201
    assert mutate(b, "POST", f"/community/posts/{post['id']}/reports", payload).json()["id"] == report.json()["id"]
    assert b.get("/api/v1/community/moderation/reports").status_code == 403
    assert mutate(b, "POST", f"/community/moderation/posts/{post['id']}", {"decision": "hide", "reason": "越权"}).status_code == 403
    login(client)
    assert client.get("/api/v1/community/moderation/reports").json()["items"][0]["id"] == report.json()["id"]
    response = mutate(client, "POST", "/community/moderation/reports/" + report.json()["id"] + "/resolve", {"decision": "hide", "reason": "违反社区规则"})
    assert response.status_code == 200 and response.json()["status"] == "hidden"
    for reader in (a, b, client):
        assert reader.get("/api/v1/community/posts/" + post["id"]).status_code == 404
        assert reader.get(asset_url).status_code == 404
        assert reader.get(f"/api/v1/community/posts/{post['id']}/comments").status_code == 404
    assert b.get("/api/v1/community/bookmarks").json()["items"] == []
    assert b.get("/api/v1/community/posts?mode=recommended").json()["items"] == []
    assert client.get("/api/v1/notes/" + note["id"]).status_code == 404
    assert client.get(f"/api/v1/community/posts/{post['id']}/draft").status_code == 404
    current = draft(a, post)
    assert current["moderation_reason"] == "违反社区规则"
    assert mutate(a, "POST", f"/community/posts/{post['id']}/publish", {"revision": current["revision"], "request_key": str(uuid4())}).status_code == 409
    assert mutate(a, "POST", f"/community/posts/{post['id']}/withdraw", {"revision": current["revision"]}).status_code == 409
    assert mutate(client, "POST", f"/community/moderation/posts/{post['id']}", {"decision": "restore", "reason": "复核通过"}).status_code == 204
    assert b.get(asset_url).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action.in_(["community.hide", "community.restore"]))) == 2


def test_disabled_authors_hide_posts_assets_comments_and_bookmarks(people):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    note, _ = source_image(a, ids["alice"])
    post = published(a, source_note_id=note["id"], source_revision=1, plain_text="")
    output = b.get("/api/v1/community/posts/" + post["id"]).json()
    mutate(b, "PUT", f"/community/posts/{post['id']}/bookmark", {})
    with SessionLocal.begin() as db:
        db.get(User, UUID(ids["alice"])).status = "disabled"
    assert b.get("/api/v1/community/posts").json()["items"] == []
    assert b.get("/api/v1/community/bookmarks").json()["items"] == []
    assert b.get("/api/v1/community/posts/" + post["id"]).status_code == 404
    assert b.get(output["assets"][0]["image_url"]).status_code == 404
    assert b.get(f"/api/v1/community/posts/{post['id']}/comments").status_code == 404


def test_disabled_commenters_are_not_counted_or_shown(people):
    clients, ids = people
    a, b, c = clients["alice"], clients["bob"], clients["charlie"]
    post = published(a)
    mutate(b, "POST", f"/community/posts/{post['id']}/comments", {"body": "评论", "request_key": str(uuid4())})
    mutate(b, "PUT", f"/community/posts/{post['id']}/like", {})
    with SessionLocal.begin() as db:
        db.get(User, UUID(ids["bob"])).status = "disabled"
    assert c.get(f"/api/v1/community/posts/{post['id']}/comments").json()["items"] == []
    output = c.get("/api/v1/community/posts/" + post["id"]).json()
    assert output["comment_count"] == output["like_count"] == 0


def test_owner_disabled_before_worker_runs_cannot_publish(people):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    post = create(a)
    submit(a, post)
    claimed = jobs.claim()
    with SessionLocal.begin() as db:
        db.get(User, UUID(ids["alice"])).status = "disabled"
    assert publishing.process_job(claimed) is False
    assert b.get("/api/v1/community/posts").json()["items"] == []
    with SessionLocal() as db:
        assert db.get(Job, claimed["id"]).status == "failed"
        assert db.scalar(select(CommunitySnapshot.status)) == "cancelled"


def test_concurrent_like_requests_create_one_record(people):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    post = published(a)
    csrf = b.get("/api/v1/auth/csrf").json()["csrf_token"]
    barrier = Barrier(2)

    def send(_):
        barrier.wait(timeout=5)
        return b.put(f"/api/v1/community/posts/{post['id']}/like", json={}, headers={"Origin": "http://testserver", "X-CSRF-Token": csrf})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, range(2)))
    assert [row.status_code for row in results] == [204, 204]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(CommunityReaction)) == 1


@pytest.mark.parametrize("bad", [{"title": " "}, {"post_type": "invalid"}, {"tags": ["x", "x"]}, {"tags": ["a\x00b"]}, {"plain_text": "a\x00b"}, {"source_note_id": str(uuid4())}, {"tags": ["x" * 33]}])
def test_post_content_validation_rejects_invalid_data_without_rows(people, bad):
    clients, ids = people
    assert mutate(clients["alice"], "POST", "/community/posts", {"title": "测试", **bad}).status_code == 422
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(CommunityPost)) == 0


@pytest.mark.parametrize("path", ["/community/posts", "/community/posts/mine", "/community/bookmarks", "/community/moderation/reports"])
def test_anonymous_community_requires_login(client, path):
    assert client.get("/api/v1" + path).status_code == 401


@pytest.mark.parametrize("status,completed,total", [("uploading",0,None),("queued",0,2),("processing",1,2),("rejected",1,2),("ready",1,2)])
def test_partial_or_pending_pdf_source_cannot_publish_even_with_ready_pages(people,status,completed,total):
    from app.modules.files.models import PdfImport
    clients,ids=people
    note,_=source_image(clients['alice'],ids['alice'])
    with SessionLocal.begin() as db:
        original=File(owner_id=ids['alice'],object_key=f'pdf/{uuid4()}/original',filename='合成.pdf',mime='application/pdf',expected_bytes=10,bytes=10,sha256='a'*64,status='ready')
        db.add(original);db.flush()
        db.add(PdfImport(owner_id=ids['alice'],note_id=note['id'],source_file_id=original.id,request_hash='b'*64,status=status,page_count=total,completed_pages=completed))
    post=create(clients['alice'],source_note_id=note['id'],source_revision=note['revision'])
    response=mutate(clients['alice'],'POST',f"/community/posts/{post['id']}/publish",{'revision':post['revision'],'request_key':str(uuid4())})
    assert response.status_code==409 and response.json()['code']=='source_not_ready'
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(CommunitySnapshot))==0
        assert db.scalar(select(func.count()).select_from(CommunityAsset))==0


def test_independent_copy_can_be_edited_and_republished_after_private_note_and_bytes_removed(people):
    from app.modules.files.models import NotePage, FileVariant
    clients,ids=people
    author,reader=clients['alice'],clients['bob']
    note,_=source_image(author,ids['alice'])
    post=published(author,source_note_id=note['id'],source_revision=note['revision'])
    with SessionLocal() as db:
        file=db.scalar(select(File).join(NotePage,NotePage.file_id==File.id).where(NotePage.note_id==note['id']))
        private_keys=[file.object_key]+list(db.scalars(select(FileVariant.object_key).where(FileVariant.file_id==file.id)))
        previous=db.scalar(select(CommunityAsset))
        old_key=previous.object_key
        expected=storage().read(old_key)
    assert mutate(author,'DELETE','/notes/'+note['id'],{'revision':note['revision']}).status_code==204
    for key in private_keys:
        storage().delete(key)
    updated=patch(author,post,title='删除原件后的独立更新')
    assert updated.status_code==200,updated.text
    submit(author,post);run_one()
    current=reader.get('/api/v1/community/posts/'+post['id'])
    assert current.status_code==200 and current.json()['snapshot_version']==2
    assert current.json()['title']=='删除原件后的独立更新'
    image=reader.get(current.json()['assets'][0]['image_url'])
    assert image.status_code==200 and image.content==expected
    with SessionLocal() as db:
        snapshots=db.scalars(select(CommunitySnapshot).order_by(CommunitySnapshot.version)).all()
        assert len(snapshots)==2 and all(str(s.source_note_id)==note['id'] for s in snapshots)
        assert len({a.object_key for a in db.scalars(select(CommunityAsset))})==2


def test_existing_independent_lineage_does_not_allow_switch_to_foreign_source(people):
    clients,ids=people
    note,_=source_image(clients['alice'],ids['alice'])
    post=published(clients['alice'],source_note_id=note['id'],source_revision=note['revision'])
    other,_=source_image(clients['bob'],ids['bob'])
    response=patch(clients['alice'],post,source_note_id=other['id'],source_revision=other['revision'])
    assert response.status_code==404
