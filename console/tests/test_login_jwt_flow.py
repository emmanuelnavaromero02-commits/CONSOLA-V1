from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.services.jwt_auth import create_access_token, decode_access_token


JWT_SECRET = "test_jwt_secret_key_with_more_than_32_chars"
INTERNAL_KEY = "test_internal_api_key_with_more_than_32_chars"


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


async def _noop_async(*args, **kwargs):
    return None


@pytest.fixture()
def console_main(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", INTERNAL_KEY)
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15")

    auth_stub = _module()
    auth_stub.COOKIE_NAME = "mod_session"
    auth_stub.cookie_secure = lambda: False

    async def authenticate(email, password):
        return {
            "id": 42,
            "email": email,
            "name": "Test Analyst",
            "role": "analyst",
            "is_active": True,
            "must_change_password": False,
        }

    async def create_session(user_id, ip=None):
        return "legacy-session-token", datetime.now(timezone.utc) + timedelta(days=7)

    async def get_session_user(token):
        return None

    async def destroy_session(token):
        return None

    def verify_internal_api_key(*args, **kwargs):
        return None

    auth_stub.authenticate = authenticate
    auth_stub.create_session = create_session
    auth_stub.get_session_user = get_session_user
    auth_stub.destroy_session = destroy_session
    auth_stub.verify_internal_api_key = verify_internal_api_key

    service_stubs = {
        "app.services.auth": auth_stub,
        "app.services.tokens": _module(),
        "app.services.email_service": _module(),
        "app.services.mcp_registry": _module(startup=_noop_async, health_check_all=_noop_async),
        "app.services.assistant": _module(),
        "app.services.studio_assistant": _module(),
        "app.services.token_store": _module(),
        "app.services.job_service": _module(),
        "app.services.cartridge_service": _module(),
    }
    for name, mod in service_stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setitem(sys.modules, "asyncpg", _module())

    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")
    main._RATE_BUCKETS.clear()
    yield main
    sys.modules.pop("app.main", None)


def test_login_success_returns_access_token(console_main):
    client = TestClient(console_main.app)

    response = client.post("/auth/login", json={"email": "analyst@example.com", "password": "correct-password"})

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "analyst@example.com"
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert "mod_session=legacy-session-token" in response.headers["set-cookie"]


def test_login_issued_token_decodes(console_main):
    client = TestClient(console_main.app)
    response = client.post("/auth/login", json={"email": "analyst@example.com", "password": "correct-password"})

    decoded = decode_access_token(response.json()["access_token"])

    assert decoded["sub"] == "42"
    assert decoded["email"] == "analyst@example.com"
    assert decoded["role"] == "analyst"


def test_me_jwt_accepts_valid_token(console_main):
    client = TestClient(console_main.app)
    token = create_access_token({"sub": "42", "email": "analyst@example.com", "role": "analyst"})

    response = client.get("/auth/me-jwt", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["claims"]["sub"] == "42"
    assert response.json()["claims"]["email"] == "analyst@example.com"
    assert response.json()["claims"]["role"] == "analyst"


def test_me_jwt_rejects_invalid_token(console_main):
    client = TestClient(console_main.app)

    response = client.get("/auth/me-jwt", headers={"Authorization": "Bearer invalid-token"})

    assert response.status_code == 401
