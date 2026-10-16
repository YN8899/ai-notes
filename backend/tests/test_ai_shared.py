import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.modules.ai import providers
from app.modules.ai.models import AiCredential
from app.shared.config import Settings, settings
from app.shared.db import SessionLocal, utcnow
from app.shared.models import Audit, AuthSession, User
from app.shared.security import hasher
from conftest import login, mutate

TEST_KEY = "sk-test-only-shared-not-a-real-provider-key"


@pytest.fixture(autouse=True)
def isolated_shared_configuration(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", TEST_KEY)
    settings.cache_clear()

    def forbid_live_requests():
        raise AssertionError("Install a mock transport; live provider access is forbidden in tests")

    monkeypatch.setattr(providers, "create_client", forbid_live_requests)
    yield
    settings.cache_clear()


def mock_provider(monkeypatch, handler):
    monkeypatch.setattr(providers, "create_client", lambda: httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False,
    ))


def test_catalog_requires_login_and_returns_only_shared_status_and_capabilities(client):
    assert client.get("/api/v1/ai/providers").status_code == 401
    login(client)
    response = client.get("/api/v1/ai/providers")
    data = response.json()
    assert set(data) == {"credential_source", "platform_enabled", "providers"}
    assert data["credential_source"] == "shared" and data["platform_enabled"] is True
    assert TEST_KEY not in response.text and TEST_KEY not in repr(settings())
    assert [provider["id"] for provider in data["providers"]] == ["deepseek"]
    models = {model["id"]: model for model in data["providers"][0]["models"]}
    assert set(models) == {"deepseek-flash", "deepseek-v4-pro"}
    assert models["deepseek-flash"]["supports_vision"] is True
    assert models["deepseek-v4-pro"]["supports_vision"] is False
    assert not any(model["supports_embedding"] for model in models.values())
    assert mutate(client, "POST", "/ai/runs", {}).status_code == 404


def test_member_uses_the_same_shared_configuration_without_own_credentials(client):
    login(client)
    admin_catalog = client.get("/api/v1/ai/providers").json()
    with SessionLocal.begin() as db:
        db.add(User(username="alice", display_name="Alice", role="user", password_hash=hasher.hash("member-password")))
    login(client, "alice", "member-password")
    assert client.get("/api/v1/ai/providers").json() == admin_catalog
    assert mutate(client, "POST", "/ai/connection-test", {}).status_code == 403
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AiCredential)) == 0


def test_personal_key_routes_are_removed(client):
    login(client)
    for method, path in [("GET", "/ai/credentials"), ("PUT", "/ai/credentials/deepseek"),
                         ("DELETE", "/ai/credentials/00000000-0000-0000-0000-000000000000"),
                         ("POST", "/ai/credentials/00000000-0000-0000-0000-000000000000/test")]:
        response = mutate(client, method, path, {"api_key": TEST_KEY, "revision": 1})
        assert response.status_code == 404 and TEST_KEY not in response.text


def test_missing_shared_key_disables_models_but_preserves_notes_app(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    settings.cache_clear()
    login(client)
    assert client.get("/api/v1/health/ready").status_code == 200
    assert client.get("/api/v1/ai/providers").json()["platform_enabled"] is False
    response = mutate(client, "POST", "/ai/connection-test", {})
    assert response.status_code == 503 and response.json()["code"] == "ai_not_configured"


@pytest.mark.parametrize("value", ["short", "a" * 513, "sk-invalid value-with-spaces",
                                   "sk-control\r\nInjected-header", "中文密钥不能作为请求头"])
def test_invalid_server_key_fails_configuration_without_echo(value):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, deepseek_api_key=value)
    assert value not in str(error.value)


def test_connection_test_requires_login_csrf_and_origin(client):
    assert mutate(client, "POST", "/ai/connection-test", {}).status_code == 401
    login(client)
    assert client.post("/api/v1/ai/connection-test", json={}, headers={"Origin": "http://testserver"}).status_code == 403
    assert client.post("/api/v1/ai/connection-test", json={}).status_code == 403


def test_connection_uses_server_key_fixed_host_and_controlled_models_without_leaking(client, monkeypatch, caplog):
    login(client)
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == providers.MODELS_URL and request.method == "GET"
        assert request.headers["Authorization"] == "Bearer " + TEST_KEY
        return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}, {"id": "deepseek-v4-pro"}, {"id": "unapproved-model"}]})

    mock_provider(monkeypatch, handler)
    response = mutate(client, "POST", "/ai/connection-test", {"api_key": "ignored-client-key", "base_url": "http://127.0.0.1"})
    assert response.status_code == 200
    assert response.json() == {"status": "valid", "code": "connected", "available_models": ["deepseek-flash", "deepseek-v4-pro"]}
    assert len(requests) == 1
    assert TEST_KEY not in response.text and TEST_KEY not in caplog.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AiCredential)) == 0
        entries = list(db.scalars(select(Audit).where(Audit.action == "ai.connection.tested")))
        assert len(entries) == 1 and entries[0].details == {"provider": "deepseek", "code": "connected"}


@pytest.mark.parametrize("status,expected_status,code", [
    (401, "invalid", "credential_rejected"), (403, "invalid", "credential_rejected"),
    (429, "unavailable", "provider_rate_limited"), (500, "unavailable", "provider_unavailable"),
    (302, "unavailable", "provider_unavailable"),
])
def test_provider_failures_are_sanitized_without_redirect_or_retry(client, monkeypatch, caplog, status, expected_status, code):
    login(client)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"error": TEST_KEY}, headers={"Location": "https://not-allowed.invalid"})

    mock_provider(monkeypatch, handler)
    response = mutate(client, "POST", "/ai/connection-test", {})
    assert response.status_code == 200
    assert response.json() == {"status": expected_status, "code": code, "available_models": []}
    assert TEST_KEY not in response.text and TEST_KEY not in caplog.text
    assert len(requests) == 1


def test_network_errors_are_sanitized_and_not_retried(client, monkeypatch, caplog):
    login(client)
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout(TEST_KEY, request=request)

    mock_provider(monkeypatch, handler)
    response = mutate(client, "POST", "/ai/connection-test", {})
    assert response.status_code == 200 and response.json()["code"] == "provider_unreachable"
    assert TEST_KEY not in response.text and TEST_KEY not in caplog.text
    assert len(requests) == 1


@pytest.mark.parametrize("content,code", [(b"not-json", "provider_invalid_response"), (b"{}", "provider_invalid_response"),
                                          (b'{"data": []}', "models_unavailable"), (b"x" * 70000, "provider_invalid_response")],
                         ids=["invalid-json", "missing-data", "empty-models", "oversized"])
def test_invalid_and_oversized_responses_are_bounded(client, monkeypatch, content, code):
    login(client)
    mock_provider(monkeypatch, lambda request: httpx.Response(200, content=content))
    response = mutate(client, "POST", "/ai/connection-test", {})
    assert response.status_code == 200 and response.json()["code"] == code


@pytest.mark.parametrize("change,status", [("disable", 401), ("logout", 401), ("demote", 403)])
def test_inflight_test_rechecks_session_status_and_admin_permission(client, monkeypatch, change, status):
    login(client)

    def handler(request):
        with SessionLocal.begin() as db:
            user = db.scalar(select(User).where(User.username == "admin"))
            if change == "disable":
                user.status = "disabled"
            elif change == "demote":
                user.role = "user"
            else:
                for session in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
                    session.revoked_at = utcnow()
        return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}]})

    mock_provider(monkeypatch, handler)
    response = mutate(client, "POST", "/ai/connection-test", {})
    assert response.status_code == status
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == "ai.connection.tested")) == 0
