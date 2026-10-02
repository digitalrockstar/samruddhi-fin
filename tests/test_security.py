import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, production_problems
from app.security import BasicAuthMiddleware


def _client():
    app = FastAPI()
    app.add_middleware(BasicAuthMiddleware, username="u", password="p")

    @app.get("/health")
    def health(): return {"ok": 1}

    @app.post("/webhook/telegram")
    def hook(): return {"ok": 1}

    @app.get("/api/x")
    def api(): return {"ok": 1}

    return TestClient(app)


def _basic(u, p):
    return {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()}


def test_open_paths_need_no_login():
    c = _client()
    assert c.get("/health").status_code == 200
    assert c.post("/webhook/telegram").status_code == 200


@pytest.mark.parametrize("headers", [{}, _basic("u", "bad"), _basic("bad", "p"), {"Authorization": "Basic !!!"}, {"Authorization": "Bearer x"}])
def test_protected_rejects_bad_credentials(headers):
    r = _client().get("/api/x", headers=headers)
    assert r.status_code == 401
    assert "Basic" in r.headers["www-authenticate"]


def test_protected_accepts_good_credentials():
    assert _client().get("/api/x", headers=_basic("u", "p")).status_code == 200


def test_production_requires_password_and_webhook_secret():
    s = Settings(database_url="sqlite://", app_env="production", secret_key="x" * 40, telegram_bot_token="t")
    assert len(production_problems(s)) == 2
    ok = Settings(database_url="sqlite://", app_env="production", secret_key="x" * 40,
                  telegram_bot_token="t", app_password="pw", telegram_webhook_secret="s")
    assert production_problems(ok) == []
    assert production_problems(Settings(database_url="sqlite://")) == []


def test_neon_url_options_are_translated_for_asyncpg():
    base = "postgresql://u:p@ep-x.ap-southeast-1.aws.neon.tech/db"
    got = Settings(database_url=base + "?sslmode=require&channel_binding=require").async_database_url
    assert got == "postgresql+asyncpg://u:p@ep-x.ap-southeast-1.aws.neon.tech/db?ssl=require"
    assert Settings(database_url=base).async_database_url == "postgresql+asyncpg://u:p@ep-x.ap-southeast-1.aws.neon.tech/db"
    assert "sslmode" not in Settings(database_url="postgres://u:p@h/db?sslmode=require").async_database_url
    assert Settings(database_url="sqlite+aiosqlite:///./x.db").async_database_url == "sqlite+aiosqlite:///./x.db"
