import base64
from datetime import timedelta
import json
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.main import app
from app.modules.ai import crypto, providers
from app.modules.ai.models import AiCredential
from app.shared.config import Settings, settings
from app.shared.db import SessionLocal, utcnow
from app.shared.errors import AppError
from app.shared.models import Audit, AuthSession, User
from app.shared.security import hasher
from conftest import login, mutate

TEST_MASTER = base64.urlsafe_b64encode(b"a" * 32).decode()
TEST_KEY = "sk-test-only-not-a-real-provider-key-1234"
NEXT_KEY = "sk-test-only-replacement-not-real-5678"


@pytest.fixture(autouse=True)
def isolated_ai_configuration(monkeypatch):
    monkeypatch.setenv("AI_CREDENTIAL_ENCRYPTION_KEY", TEST_MASTER)
    settings.cache_clear()

    def forbid_live_requests():
        raise AssertionError("AI tests must install a mock transport; live provider access is forbidden")

    monkeypatch.setattr(providers, "create_client", forbid_live_requests)
    yield
    settings.cache_clear()


def save(client, key=TEST_KEY, revision=None):
    return mutate(client, "PUT", "/ai/credentials/deepseek", {"api_key": key, "revision": revision})


def create_credential(client):
    login(client)
    response = save(client)
    assert response.status_code == 200
    return response.json()


def mock_provider(monkeypatch, handler):
    monkeypatch.setattr(providers, "create_client", lambda: httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False,
    ))


def test_catalog_is_authenticated_controlled_and_platform_disabled(client):
    assert client.get("/api/v1/ai/providers").status_code == 401
    login(client)
    data = client.get("/api/v1/ai/providers").json()
    assert data["storage_ready"] is True
    assert data["platform_enabled"] is False
    assert [provider["id"] for provider in data["providers"]] == ["deepseek"]
    models = {model["id"]: model for model in data["providers"][0]["models"]}
    assert set(models) == {"deepseek-flash", "deepseek-v4-pro"}
    assert models["deepseek-flash"]["supports_vision"] is True
    assert models["deepseek-v4-pro"]["supports_vision"] is False
    assert not any(model["supports_embedding"] for model in models.values())
    assert mutate(client, "POST", "/ai/runs", {}).status_code == 404


def test_key_is_encrypted_and_never_returned_or_audited_in_plaintext(client, caplog):
    credential = create_credential(client)
    assert credential["masked_key"] == "••••••••1234"
    assert TEST_KEY not in json.dumps(credential)
    assert "ciphertext" not in credential and "api_key" not in credential and "owner_id" not in credential
    with SessionLocal() as db:
        row = db.get(AiCredential, UUID(credential["id"]))
        assert TEST_KEY.encode() not in row.ciphertext
        assert crypto.decrypt_key(row.ciphertext, row.owner_id, row.provider, row.id, row.key_version) == TEST_KEY
        assert TEST_KEY not in repr([entry.details for entry in db.scalars(select(Audit))])
    listing = client.get("/api/v1/ai/credentials")
    assert listing.status_code == 200 and TEST_KEY not in listing.text
    assert TEST_KEY not in caplog.text


def test_replace_and_delete_require_the_current_revision(client):
    first = create_credential(client)
    assert save(client, NEXT_KEY).status_code == 409
    replaced = save(client, NEXT_KEY, first["revision"])
    assert replaced.status_code == 200
    current = replaced.json()
    assert current["id"] == first["id"] and current["revision"] == 2
    assert current["masked_key"] == "••••••••5678"
    assert current["test_status"] is None and current["available_models"] == []
    assert save(client, TEST_KEY, first["revision"]).status_code == 409
    assert mutate(client, "DELETE", "/ai/credentials/" + current["id"], {"revision": 1}).status_code == 409
    assert mutate(client, "DELETE", "/ai/credentials/" + current["id"], {"revision": 2}).status_code == 204
    assert client.get("/api/v1/ai/credentials").json() == []
    with SessionLocal() as db:
        assert db.get(AiCredential, UUID(first["id"])) is None


def test_admin_cannot_test_or_delete_another_users_key(client):
    with SessionLocal.begin() as db:
        db.add(User(username="alice", display_name="Alice", role="user", password_hash=hasher.hash("member-password")))
    with TestClient(app) as alice:
        login(alice, "alice", "member-password")
        credential = save(alice).json()
    login(client)
    assert client.get("/api/v1/ai/credentials").json() == []
    for method, suffix in [("POST", "/test"), ("DELETE", "")]:
        response = mutate(client, method, "/ai/credentials/" + credential["id"] + suffix, {"revision": 1})
        assert response.status_code == 404
        assert TEST_KEY not in response.text


@pytest.mark.parametrize("key", ["short", "a" * 513, "sk-invalid value-with-spaces", "sk-control\r\nInjected-header", "中文模型密钥不能作为请求头使用"])
def test_invalid_key_inputs_are_rejected_without_echoing_them(client, key):
    login(client)
    response = save(client, key)
    assert response.status_code == 422
    assert key not in response.text


def test_custom_hosts_and_unknown_providers_are_not_accepted(client):
    login(client)
    assert mutate(client, "PUT", "/ai/credentials/custom", {"api_key": TEST_KEY}).status_code == 404
    response = mutate(client, "PUT", "/ai/credentials/deepseek", {"api_key": TEST_KEY, "base_url": "http://127.0.0.1"})
    assert response.status_code == 422
    assert TEST_KEY not in response.text


def test_missing_encryption_secret_disables_storage_but_not_the_app(client, monkeypatch):
    monkeypatch.setenv("AI_CREDENTIAL_ENCRYPTION_KEY", "")
    settings.cache_clear()
    login(client)
    assert client.get("/api/v1/health/ready").status_code == 200
    assert client.get("/api/v1/ai/providers").json()["storage_ready"] is False
    assert save(client).status_code == 503
    assert client.get("/api/v1/ai/credentials").json() == []


@pytest.mark.parametrize("value", ["invalid-secret", "a" * 44, "私密值不能明文回显到错误中"])
def test_malformed_master_secret_is_rejected_without_echo(value):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, ai_credential_encryption_key=value)
    assert value not in str(error.value)


def test_ciphertext_authenticates_identity_version_and_integrity():
    owner, credential_id = uuid4(), uuid4()
    ciphertext = crypto.encrypt_key(TEST_KEY, owner, "deepseek", credential_id)
    assert crypto.encrypt_key(TEST_KEY, owner, "deepseek", credential_id) != ciphertext
    for data, other_owner, provider, other_id, version in [
        (ciphertext, uuid4(), "deepseek", credential_id, 1),
        (ciphertext, owner, "other", credential_id, 1),
        (ciphertext, owner, "deepseek", uuid4(), 1),
        (ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]), owner, "deepseek", credential_id, 1),
        (ciphertext, owner, "deepseek", credential_id, 2),
    ]:
        with pytest.raises(AppError) as error:
            crypto.decrypt_key(data, other_owner, provider, other_id, version)
        assert error.value.status == 503


def test_connection_uses_only_official_models_endpoint_and_filters_models(client, monkeypatch):
    credential = create_credential(client)
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == providers.MODELS_URL
        assert request.method == "GET" and request.headers["Authorization"] == "Bearer " + TEST_KEY
        return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}, {"id": "deepseek-v4-pro"}, {"id": "unapproved-model"}]})

    mock_provider(monkeypatch, handler)
    response = mutate(client, "POST", "/ai/credentials/" + credential["id"] + "/test", {"revision": 1})
    assert response.status_code == 200
    assert response.json()["test_status"] == "valid"
    assert response.json()["available_models"] == ["deepseek-flash", "deepseek-v4-pro"]
    assert TEST_KEY not in response.text
    assert mutate(client, "POST", "/ai/credentials/" + credential["id"] + "/test", {"revision": 1}).status_code == 429
    assert len(requests) == 1


@pytest.mark.parametrize("status,expected_status,code", [
    (401, "invalid", "credential_rejected"), (403, "invalid", "credential_rejected"),
    (429, "unavailable", "provider_rate_limited"), (500, "unavailable", "provider_unavailable"),
    (302, "unavailable", "provider_unavailable"),
])
def test_provider_failures_are_sanitized_and_never_follow_redirects(client, monkeypatch, caplog, status, expected_status, code):
    credential = create_credential(client)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"error": TEST_KEY}, headers={"Location": "https://not-allowed.invalid"})

    mock_provider(monkeypatch, handler)
    response = mutate(client, "POST", "/ai/credentials/" + credential["id"] + "/test", {"revision": 1})
    assert response.status_code == 200
    assert response.json()["test_status"] == expected_status
    assert response.json()["test_code"] == code
    assert TEST_KEY not in response.text and TEST_KEY not in caplog.text
    assert len(requests) == 1


def test_network_errors_are_sanitized_and_not_retried(client, monkeypatch, caplog):
    credential = create_credential(client)
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout(TEST_KEY, request=request)

    mock_provider(monkeypatch, handler)
    response = mutate(client, "POST", "/ai/credentials/" + credential["id"] + "/test", {"revision": 1})
    assert response.json()["test_code"] == "provider_unreachable"
    assert TEST_KEY not in response.text and TEST_KEY not in caplog.text
    assert len(requests) == 1


@pytest.mark.parametrize("content,code", [(b"not-json", "provider_invalid_response"), (b"{}", "provider_invalid_response"),
                                          (b'{"data": []}', "models_unavailable"), (b"x" * 70000, "provider_invalid_response")],
                         ids=["invalid-json", "missing-data", "empty-models", "oversized"])
def test_invalid_and_oversized_model_responses_are_bounded(client, monkeypatch, content, code):
    credential = create_credential(client)
    mock_provider(monkeypatch, lambda request: httpx.Response(200, content=content))
    response = mutate(client, "POST", "/ai/credentials/" + credential["id"] + "/test", {"revision": 1})
    assert response.status_code == 200 and response.json()["test_code"] == code


@pytest.mark.parametrize("change", ["replace", "delete", "disable", "logout", "retest"])
def test_inflight_connection_result_cannot_overwrite_changed_state(client, monkeypatch, change):
    credential = create_credential(client)

    def handler(request):
        with SessionLocal.begin() as db:
            row = db.get(AiCredential, UUID(credential["id"]))
            if change == "replace":
                row.ciphertext = crypto.encrypt_key(NEXT_KEY, row.owner_id, row.provider, row.id)
                row.key_suffix, row.revision = "5678", 2
            elif change == "delete":
                db.delete(row)
            elif change == "disable":
                db.get(User, row.owner_id).status = "disabled"
            elif change == "retest":
                row.last_test_started_at = utcnow() + timedelta(seconds=11)
            else:
                for session in db.scalars(select(AuthSession).where(AuthSession.user_id == row.owner_id)):
                    session.revoked_at = utcnow()
        return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}]})

    mock_provider(monkeypatch, handler)
    response = mutate(client, "POST", "/ai/credentials/" + credential["id"] + "/test", {"revision": 1})
    assert response.status_code == {"replace": 409, "delete": 404, "disable": 401, "logout": 401, "retest": 409}[change]
    with SessionLocal() as db:
        row = db.get(AiCredential, UUID(credential["id"]))
        if row:
            assert row.last_tested_at is None and row.test_status is None
            if change == "replace":
                assert row.revision == 2 and row.key_suffix == "5678"
