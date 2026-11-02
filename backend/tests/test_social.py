"""T25 real HTTP permissions, canonical relationships and immediate revocation."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import io
from threading import Barrier
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import func, select

from app.main import app
from app.modules.files.models import File, FileVariant, NotePage
from app.modules.files.service import storage
from app.modules.social.models import Follow, Friendship, NoteGrant
from app.shared.config import settings
from app.shared.db import SessionLocal, utcnow
from app.shared.models import User
from app.shared.security import hasher
from conftest import login, mutate


@pytest.fixture
def people(tmp_path):
    settings.cache_clear()
    original = settings().storage_root
    settings().storage_root = str(tmp_path / "objects")
    ids = {}
    with SessionLocal.begin() as db:
        for name in ("alice", "bob", "charlie"):
            user = User(username=name, display_name=name.title(), role="user", password_hash=hasher.hash("member-password"))
            db.add(user)
            db.flush()
            ids[name] = str(user.id)
    with ExitStack() as stack:
        clients = {}
        for name in ids:
            client = stack.enter_context(TestClient(app))
            login(client, name, "member-password")
            clients[name] = client
        yield clients, ids
    settings().storage_root = original
    settings.cache_clear()


def request(a, bid):
    response = mutate(a, "POST", "/social/friend-requests", {"user_id": bid})
    assert response.status_code == 201, response.text
    return response.json()


def befriend(a, b, bid):
    row = request(a, bid)
    response = mutate(b, "POST", f"/social/friend-requests/{row['id']}/respond", {"decision": "accept", "revision": row["revision"]})
    assert response.status_code == 200, response.text
    return response.json()


def create_note(client, **kwargs):
    response = mutate(client, "POST", "/notes", {"title": "私人学习资料", "plain_text": "只有好友获授权后可读", **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def share(a, note, bid):
    response = mutate(a, "POST", "/social/shares", {"note_id": note["id"], "grantee_id": bid, "note_revision": note["revision"]})
    assert response.status_code == 201, response.text
    return response.json()


def profile_patch(client, revision=1, **kwargs):
    return mutate(client, "PATCH", "/social/profile", {"display_name": "新名字", "bio": "简介", "subject": "数学", "details": "好友专享详情", "visibility": "friends", "revision": revision, **kwargs})


def test_member_search_returns_only_basic_identity_and_literal_handle(people):
    clients, ids = people
    profile_patch(clients["alice"])
    bob = clients["bob"]
    response = bob.get("/api/v1/social/members", params={"q": "alice"})
    assert response.status_code == 200
    assert response.json()["items"] == [{"id": ids["alice"], "username": "alice", "display_name": "新名字", "relationship": "none", "following": False}]
    assert bob.get("/api/v1/social/members", params={"q": "%"}).json()["items"] == []
    assert bob.get("/api/v1/social/members", params={"q": "数学"}).json()["items"] == []


def test_friend_requests_authority_status_revision_and_duplicate(people):
    clients, ids = people
    a, b, c = (clients[n] for n in ("alice", "bob", "charlie"))
    row = request(a, ids["bob"])
    assert request(a, ids["bob"])["id"] == row["id"]
    assert b.get("/api/v1/social/friend-requests").json()["items"][0]["member"]["id"] == ids["alice"]
    assert a.get("/api/v1/social/friend-requests?direction=outgoing").json()["items"][0]["id"] == row["id"]
    path = f"/social/friend-requests/{row['id']}/respond"
    for client in (a, c):
        assert mutate(client, "POST", path, {"decision": "accept", "revision": 1}).status_code == 404
    assert mutate(b, "POST", "/social/friend-requests", {"user_id": ids["alice"]}).status_code == 409
    assert mutate(b, "POST", path, {"decision": "accept", "revision": 2}).status_code == 409
    accepted = mutate(b, "POST", path, {"decision": "accept", "revision": 1})
    assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"
    assert mutate(b, "POST", path, {"decision": "accept", "revision": 2}).status_code == 409
    assert a.get("/api/v1/social/friends").json()["items"][0]["member"]["relationship"] == "friend"
    assert mutate(a, "POST", "/social/friend-requests", {"user_id": ids["bob"]}).status_code == 409


def test_rejected_request_can_be_explicitly_requested_again(people):
    clients, ids = people
    row = request(clients["alice"], ids["bob"])
    rejected = mutate(clients["bob"], "POST", f"/social/friend-requests/{row['id']}/respond", {"decision": "reject", "revision": 1})
    assert rejected.status_code == 200
    assert clients["alice"].get("/api/v1/social/friends").json()["items"] == []
    again = request(clients["bob"], ids["alice"])
    assert again["id"] == row["id"] and again["requester_id"] == ids["bob"] and again["revision"] == 3


def test_profiles_default_private_to_nonfriends_and_admin(people, client):
    clients, ids = people
    assert profile_patch(clients["alice"]).status_code == 200
    login(client)
    for viewer in (clients["bob"], clients["charlie"], client):
        profile = viewer.get("/api/v1/social/members/" + ids["alice"]).json()
        assert profile["details_visible"] is False
        assert all(profile[key] is None for key in ("bio", "subject", "details", "visibility", "revision"))
    befriend(clients["alice"], clients["bob"], ids["bob"])
    profile = clients["bob"].get("/api/v1/social/members/" + ids["alice"]).json()
    assert profile["details_visible"] is True and profile["details"] == "好友专享详情"
    assert profile["visibility"] is None and profile["revision"] is None
    assert profile_patch(clients["alice"], revision=2, visibility="private").status_code == 200
    assert clients["bob"].get("/api/v1/social/members/" + ids["alice"]).json()["details_visible"] is False
    assert clients["alice"].get("/api/v1/social/profile").json()["details"] == "好友专享详情"


def test_profile_conflict_preserves_name_and_invalid_input_rejected(people):
    clients, ids = people
    a = clients["alice"]
    assert profile_patch(a).status_code == 200
    assert profile_patch(a, display_name="过时覆盖").status_code == 409
    assert a.get("/api/v1/auth/me").json()["display_name"] == "新名字"
    assert profile_patch(a, revision=2, display_name="   ").status_code == 422
    assert profile_patch(a, revision=2, details="a\x00b").status_code == 422
    assert profile_patch(a, revision=2, visibility="public").status_code == 422


def test_follow_is_idempotent_independent_from_friends_and_private_profiles(people):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    path = "/social/follows/" + ids["bob"]
    assert mutate(a, "PUT", path, {}).status_code == 204
    assert mutate(a, "PUT", path, {}).status_code == 204
    profile = a.get("/api/v1/social/members/" + ids["bob"]).json()
    assert profile["following"] is True and profile["relationship"] == "none" and profile["details_visible"] is False
    assert a.get("/api/v1/social/following").json()["items"][0]["id"] == ids["bob"]
    assert b.get("/api/v1/social/following").json()["items"] == []
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Follow)) == 1
    assert mutate(a, "DELETE", path, {}).status_code == 204
    assert mutate(a, "DELETE", path, {}).status_code == 204


@pytest.mark.parametrize("action", ["friend-requests", "follows"])
def test_self_relationship_rejected(people, action):
    clients, ids = people
    method, path, body = ("POST", "/social/friend-requests", {"user_id": ids["alice"]}) if action == "friend-requests" else ("PUT", "/social/follows/" + ids["alice"], {})
    assert mutate(clients["alice"], method, path, body).status_code == 422


def test_share_requires_real_friend_owner_and_current_note_revision(people):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    note = create_note(a)
    payload = {"note_id": note["id"], "grantee_id": ids["bob"], "note_revision": 1}
    assert mutate(a, "POST", "/social/shares", payload).status_code == 409
    assert mutate(a, "PUT", "/social/follows/" + ids["bob"], {}).status_code == 204
    assert mutate(a, "POST", "/social/shares", payload).status_code == 409
    befriend(a, b, ids["bob"])
    assert mutate(a, "POST", "/social/shares", {**payload, "note_revision": 2}).status_code == 409
    assert mutate(b, "POST", "/social/shares", {**payload, "grantee_id": ids["alice"]}).status_code == 404
    grant = share(a, note, ids["bob"])
    assert share(a, note, ids["bob"])["id"] == grant["id"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(NoteGrant)) == 1


def test_shared_text_is_live_but_never_private_metadata_or_mutation_permission(people, client):
    clients, ids = people
    a, b, c = clients["alice"], clients["bob"], clients["charlie"]
    befriend(a, b, ids["bob"])
    note = create_note(a, body_json={"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "公式学习"}]}]})
    grant = share(a, note, ids["bob"])
    path = "/api/v1/social/shares/" + grant["id"]
    visible = b.get(path)
    assert visible.status_code == 200
    assert visible.json()["plain_text"] == note["plain_text"]
    assert set(visible.json()) == {"grant", "body_json", "plain_text", "pages"}
    assert not any(key in visible.json()["grant"] for key in ("notebook_id", "tag_ids", "object_key"))
    assert b.get("/api/v1/notes/" + note["id"]).status_code == 404
    assert b.get("/api/v1/notes/" + note["id"] + "/versions").status_code == 404
    assert mutate(b, "PATCH", "/notes/" + note["id"], {"revision": 1, "title": "不能修改"}).status_code == 404
    assert mutate(b, "DELETE", "/notes/" + note["id"], {"revision": 1}).status_code == 404
    assert mutate(b, "DELETE", "/social/shares/" + grant["id"], {"revision": 1}).status_code == 404
    login(client)
    for stranger in (c, client):
        assert stranger.get(path).status_code == 404
    updated = mutate(a, "PATCH", "/notes/" + note["id"], {"revision": 1, "plain_text": "原笔记更新后的正文"})
    assert updated.status_code == 200
    assert b.get(path).json()["plain_text"] == "原笔记更新后的正文"
    assert b.get(path).json()["grant"]["source_revision"] == 2


def test_revoke_share_immediately_hides_lists_body_and_images(people):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    befriend(a, b, ids["bob"])
    note = create_note(a)
    grant = share(a, note, ids["bob"])
    assert len(b.get("/api/v1/social/shares").json()["items"]) == 1
    assert mutate(a, "DELETE", "/social/shares/" + grant["id"], {"revision": 2}).status_code == 409
    assert mutate(a, "DELETE", "/social/shares/" + grant["id"], {"revision": 1}).status_code == 204
    assert b.get("/api/v1/social/shares/" + grant["id"]).status_code == 404
    assert b.get("/api/v1/social/shares").json()["items"] == []
    assert a.get("/api/v1/social/shares?direction=sent").json()["items"] == []
    assert b.get(f"/api/v1/social/shares/{grant['id']}/pages/{uuid4()}/image").status_code == 404
    renewed = share(a, note, ids["bob"])
    assert renewed["id"] == grant["id"] and renewed["revision"] == 3


def test_unfriend_revokes_bilateral_grants_and_readd_does_not_revive_them(people):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    befriend(a, b, ids["bob"])
    own, other = create_note(a), create_note(b)
    first, second = share(a, own, ids["bob"]), share(b, other, ids["alice"])
    assert mutate(a, "DELETE", "/social/friends/" + ids["bob"], {}).status_code == 204
    for reader, grant in ((a, second), (b, first)):
        assert reader.get("/api/v1/social/shares/" + grant["id"]).status_code == 404
    befriend(b, a, ids["alice"])
    assert b.get("/api/v1/social/shares/" + first["id"]).status_code == 404
    assert a.get("/api/v1/social/friends").json()["items"][0]["member"]["id"] == ids["bob"]
    share(a, own, ids["bob"])
    assert b.get("/api/v1/social/shares/" + first["id"]).status_code == 200


@pytest.mark.parametrize("invalidated", ["owner_disabled", "recipient_disabled", "note_deleted"])
def test_shared_access_rechecks_account_and_recycle_state(people, invalidated):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    befriend(a, b, ids["bob"])
    note, grant = create_note(a), None
    grant = share(a, note, ids["bob"])
    if invalidated == "note_deleted":
        assert mutate(a, "DELETE", "/notes/" + note["id"], {"revision": 1}).status_code == 204
    else:
        target = "alice" if invalidated == "owner_disabled" else "bob"
        with SessionLocal.begin() as db:
            db.get(User, UUID(ids[target])).status = "disabled"
    response = b.get("/api/v1/social/shares/" + grant["id"])
    assert response.status_code == (401 if invalidated == "recipient_disabled" else 404)


def image_note(client, owner_id):
    note = create_note(client, kind="image", plain_text="")
    stream = io.BytesIO()
    Image.new("RGB", (24, 16), "blue").save(stream, "JPEG")
    data = stream.getvalue()
    fid, page_id = uuid4(), uuid4()
    key = f"{owner_id}/{fid}/reading.jpg"
    storage().put(key, data)
    with SessionLocal.begin() as db:
        db.add(File(id=fid, owner_id=UUID(owner_id), object_key=key + ".original", filename="学习图片.jpg", mime="image/jpeg", expected_bytes=len(data), bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), width=24, height=16, status="ready"))
        db.flush()
        db.add(FileVariant(file_id=fid, kind="reading", object_key=key, bytes=len(data)))
        db.add(NotePage(id=page_id, owner_id=UUID(owner_id), note_id=UUID(note["id"]), file_id=fid, position=0))
    return note, page_id, fid, data


def test_shared_image_exact_page_scope_and_revocation_without_private_tickets(people):
    clients, ids = people
    a, b, c = clients["alice"], clients["bob"], clients["charlie"]
    befriend(a, b, ids["bob"])
    note, page_id, fid, content = image_note(a, ids["alice"])
    second, other_page, _, _ = image_note(a, ids["alice"])
    grant = share(a, note, ids["bob"])
    shared = b.get("/api/v1/social/shares/" + grant["id"]).json()
    page = shared["pages"][0]
    assert page["id"] == str(page_id) and "file_id" not in page and "object_key" not in page
    image = b.get(page["image_url"])
    assert image.status_code == 200 and image.content == content and image.headers["cache-control"] == "no-store"
    assert c.get(page["image_url"]).status_code == 404
    assert b.get(page["image_url"].replace(str(page_id), str(other_page))).status_code == 404
    assert b.get(page["image_url"] + "?kind=original").status_code == 422
    assert mutate(b, "POST", f"/files/{fid}/ticket", {}).status_code == 404
    assert b.get("/api/v1/ai/image-notebooks/" + note["id"]).status_code == 404
    assert mutate(a, "DELETE", "/social/shares/" + grant["id"], {"revision": 1}).status_code == 204
    assert b.get(page["image_url"]).status_code == 404


@pytest.mark.parametrize("resource", ["members", "friends", "friend-requests", "following", "shares"])
def test_cursor_is_scope_bound_and_pages_do_not_repeat(people, resource):
    clients, ids = people
    a, b, c = clients["alice"], clients["bob"], clients["charlie"]
    if resource == "friends":
        befriend(a, b, ids["bob"])
        befriend(a, c, ids["charlie"])
    elif resource == "friend-requests":
        request(b, ids["alice"])
        request(c, ids["alice"])
    elif resource == "following":
        for target in ("bob", "charlie"):
            assert mutate(a, "PUT", "/social/follows/" + ids[target], {}).status_code == 204
    elif resource == "shares":
        for source in ("bob", "charlie"):
            befriend(clients[source], a, ids["alice"])
            share(clients[source], create_note(clients[source]), ids["alice"])
    path = "/api/v1/social/" + resource
    first = a.get(path, params={"limit": 1}).json()
    assert len(first["items"]) == 1 and first["next_cursor"]
    second = a.get(path, params={"limit": 1, "cursor": first["next_cursor"]})
    assert second.status_code == 200
    assert first["items"][0]["id"] != second.json()["items"][0]["id"]
    assert a.get(path, params={"cursor": "not-a-cursor"}).status_code == 422
    assert b.get(path, params={"cursor": first["next_cursor"]}).status_code == 422


def test_disabled_members_disappear_and_count_only_live_permissions(people):
    clients, ids = people
    a, b = clients["alice"], clients["bob"]
    befriend(a, b, ids["bob"])
    share(b, create_note(b), ids["alice"])
    mutate(a, "PUT", "/social/follows/" + ids["bob"], {})
    assert a.get("/api/v1/social/counts").json() == {"friends": 1, "incoming_requests": 0, "following": 1, "received_shares": 1}
    with SessionLocal.begin() as db:
        db.get(User, UUID(ids["bob"])).status = "disabled"
    assert a.get("/api/v1/social/counts").json() == {"friends": 0, "incoming_requests": 0, "following": 0, "received_shares": 0}
    assert a.get("/api/v1/social/members", params={"q": "bob"}).json()["items"] == []
    assert a.get("/api/v1/social/members/" + ids["bob"]).status_code == 404


def test_simultaneous_reverse_friend_requests_have_one_canonical_record(people):
    clients, ids = people
    barrier = Barrier(2)
    requests = []
    for source, target in (("alice", "bob"), ("bob", "alice")):
        c = clients[source]
        csrf = c.get("/api/v1/auth/csrf").json()["csrf_token"]
        requests.append((c, ids[target], csrf))

    def send(item):
        c, target_id, csrf = item
        barrier.wait(timeout=5)
        return c.post("/api/v1/social/friend-requests", json={"user_id": target_id}, headers={"Origin": "http://testserver", "X-CSRF-Token": csrf})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, requests))
    assert sorted(r.status_code for r in results) == [201, 409], [r.text for r in results]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Friendship)) == 1


@pytest.mark.parametrize("path", ["/social/profile", "/social/members", "/social/friends", "/social/shares", "/social/counts"])
def test_anonymous_social_reads_require_login(client, path):
    assert client.get("/api/v1" + path).status_code == 401
