import os
import pytest
from sqlalchemy import text

test_url = os.environ.get("TEST_DATABASE_URL")
if not test_url:
    raise RuntimeError("Set TEST_DATABASE_URL to an isolated PostgreSQL test database; tests truncate its M0 tables")
os.environ["DATABASE_URL"] = test_url
os.environ["APP_ORIGIN"] = "http://testserver"
os.environ["RATE_LIMIT_REQUESTS"] = "1000"
# A developer's private .env must never make the regression suite use real OSS.
os.environ["STORAGE_BACKEND"] = "local"
# Never let a developer's private shared model key reach regression tests.
os.environ["DEEPSEEK_API_KEY"] = ""
from app.shared.db import engine, SessionLocal, Base
from app.shared.models import User
from app.shared.security import hasher
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def clean_database():
    # Refuse known application database names: a test run is deliberately destructive.
    if engine.url.database not in {"notes_test", "postgres"}:
        raise RuntimeError("Use notes_test (or postgres in a disposable PGlite runtime)")
    with engine.begin() as c:
        names = ','.join('"'+t.name+'"' for t in Base.metadata.sorted_tables)
        c.execute(text("TRUNCATE " + names + " CASCADE"))
    with SessionLocal.begin() as db:
        db.add(User(username="admin", display_name="管理员", role="admin", password_hash=hasher.hash("admin-test-password")))
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def mutate(client, method, path, body):
    csrf = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    return client.request(method, "/api/v1"+path, json=body, headers={"Origin": "http://testserver", "X-CSRF-Token": csrf})


def login(client, username="admin", password="admin-test-password"):
    r=mutate(client,"POST","/auth/login",{"username":username,"password":password})
    assert r.status_code==200, r.text
    return r.json()


def invite(client, max_uses=1):
    login(client)
    r=mutate(client,"POST","/admin/invites",{"max_uses":max_uses,"valid_days":7,"label":"test"})
    assert r.status_code==201,r.text
    return r.json()


def register(client, code, username="alice"):
    return mutate(client,"POST","/auth/register",{"username":username,"password":"member-password","display_name":username,"invite_code":code})
