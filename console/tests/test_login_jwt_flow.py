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
    auth_stub.REFRESH_COOKIE_NAME = "refresh_token"
    auth_stub.cookie_secure = lambda: False
    auth_stub.revoked_refresh_tokens = []
    auth_stub.created_refresh_tokens = []
    auth_stub.user = {
        "id": 42,
        "email": "analyst@example.com",
        "name": "Test Analyst",
        "role": "analyst",
        "is_active": True,
        "must_change_password": False,
    }
    auth_stub.workspace_rows = [{
        "workspace_id": "11111111-1111-1111-1111-111111111111",
        "workspace_name": "Main Workspace",
        "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "tenant_name": "Default Tenant",
        "workspace_role": "analyst",
    }]

    async def authenticate(email, password):
        user = dict(auth_stub.user)
        user["email"] = email
        return user

    async def create_session(user_id, ip=None):
        return "legacy-session-token", datetime.now(timezone.utc) + timedelta(days=7)

    async def create_refresh_token(user_id):
        token = f"refresh-token-{len(auth_stub.created_refresh_tokens) + 1}"
        auth_stub.created_refresh_tokens.append((user_id, token))
        return token, datetime.now(timezone.utc) + timedelta(days=7)

    async def get_refresh_token_user(token):
        if token in {"refresh-token-1", "valid-refresh-token"} and token not in auth_stub.revoked_refresh_tokens:
            return {
                "id": 42,
                "email": "analyst@example.com",
                "name": "Test Analyst",
                "role": "analyst",
                "is_active": True,
                "must_change_password": False,
            }
        return None

    async def revoke_refresh_token(token):
        auth_stub.revoked_refresh_tokens.append(token)

    async def get_session_user(token):
        if token == "legacy-session-token":
            return dict(auth_stub.user)
        return None

    async def get_user_by_id(user_id):
        if user_id == auth_stub.user["id"]:
            return dict(auth_stub.user)
        return None

    class FakePool:
        async def fetch(self, query, user_id):
            return [dict(row) for row in auth_stub.workspace_rows]

    async def pool():
        return FakePool()

    async def destroy_session(token):
        return None

    async def list_users(active_only=True):
        return [dict(auth_stub.user)]

    def verify_internal_api_key(*args, **kwargs):
        return None

    async def close_pool():
        return None

    async def assistant_chat(message, history):
        return {"reply": f"echo:{message}", "history": history}

    async def token_summary():
        return {"total_tokens": 123}

    async def list_recent_jobs(limit=20):
        return [{"id": "job-1", "status": "ok"}]

    async def get_job(job_id):
        return {"id": job_id, "status": "ok"}

    auth_stub.authenticate = authenticate
    auth_stub.create_session = create_session
    auth_stub.create_refresh_token = create_refresh_token
    auth_stub.get_refresh_token_user = get_refresh_token_user
    auth_stub.revoke_refresh_token = revoke_refresh_token
    auth_stub.get_session_user = get_session_user
    auth_stub.get_user_by_id = get_user_by_id
    auth_stub.pool = pool
    auth_stub.destroy_session = destroy_session
    auth_stub.list_users = list_users
    auth_stub.verify_internal_api_key = verify_internal_api_key
    auth_stub.close_pool = close_pool

    service_stubs = {
        "app.services.auth": auth_stub,
        "app.services.tokens": _module(),
        "app.services.email_service": _module(),
        "app.services.mcp_registry": _module(startup=_noop_async, health_check_all=_noop_async),
        "app.services.assistant": _module(chat=assistant_chat),
        "app.services.studio_assistant": _module(),
        "app.services.token_store": _module(summary=token_summary),
        "app.services.job_service": _module(list_recent=list_recent_jobs, get=get_job),
        "app.services.cartridge_service": _module(close_pool=close_pool),
    }
    for name, mod in service_stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setitem(sys.modules, "asyncpg", _module())

    sys.modules.pop("app.main", None)
    sys.modules.pop("app.dependencies", None)
    main = importlib.import_module("app.main")
    main._RATE_BUCKETS.clear()
    yield main
    sys.modules.pop("app.main", None)
    sys.modules.pop("app.dependencies", None)


def test_login_success_returns_access_token(console_main):
    client = TestClient(console_main.app)

    response = client.post("/auth/login", json={"email": "analyst@example.com", "password": "correct-password"})

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "analyst@example.com"
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    set_cookie = response.headers.get("set-cookie", "")
    assert "mod_session=legacy-session-token" in set_cookie
    assert "refresh_token=refresh-token-1" in set_cookie
    assert "HttpOnly" in set_cookie


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


def test_me_current_accepts_valid_jwt(console_main):
    client = TestClient(console_main.app)
    token = create_access_token({"sub": "42", "email": "analyst@example.com", "role": "analyst"})

    response = client.get("/auth/me-current", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["user"]["id"] == 42
    assert response.json()["user"]["email"] == "analyst@example.com"
    assert response.json()["user"]["active_workspace_id"] == "11111111-1111-1111-1111-111111111111"
    assert response.json()["user"]["active_tenant_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert response.json()["user"]["workspace_role"] == "analyst"
    assert response.json()["user"]["workspaces"][0]["workspace_name"] == "Main Workspace"


def test_protected_route_without_jwt_or_legacy_cookie_returns_401(console_main):
    client = TestClient(console_main.app)

    response = client.get("/api/me")

    assert response.status_code == 401


def test_protected_route_with_valid_jwt_returns_200(console_main):
    client = TestClient(console_main.app)
    token = create_access_token({"sub": "42", "email": "analyst@example.com", "role": "analyst"})

    response = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["id"] == 42
    assert response.json()["workspace_role"] == "analyst"


def test_protected_route_with_legacy_cookie_still_works(console_main):
    client = TestClient(console_main.app)
    client.cookies.set("mod_session", "legacy-session-token")

    response = client.get("/api/me")

    assert response.status_code == 200
    assert response.json()["id"] == 42
    assert response.json()["workspace_role"] == "analyst"


def test_admin_route_rejects_viewer_workspace_role(console_main):
    client = TestClient(console_main.app)
    console_main._auth.user["role"] = "user"
    console_main._auth.workspace_rows[0]["workspace_role"] = "viewer"
    token = create_access_token({"sub": "42", "email": "analyst@example.com", "role": "user"})

    response = client.get("/api/admin/users", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403


def test_admin_route_allows_admin_workspace_role(console_main):
    client = TestClient(console_main.app)
    console_main._auth.user["role"] = "user"
    console_main._auth.workspace_rows[0]["workspace_role"] = "admin"
    token = create_access_token({"sub": "42", "email": "analyst@example.com", "role": "user"})

    response = client.get("/api/admin/users", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["users"][0]["id"] == 42


def test_public_route_still_works_without_token(console_main):
    client = TestClient(console_main.app)

    response = client.get("/auth/me")

    assert response.status_code == 200
    assert response.json() == {"user": None}


def test_protected_route_with_unassigned_workspace_returns_403(console_main):
    client = TestClient(console_main.app)
    token = create_access_token({"sub": "42", "email": "analyst@example.com", "role": "analyst"})

    response = client.get(
        "/api/me",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Workspace-Id": "99999999-9999-9999-9999-999999999999",
        },
    )

    assert response.status_code == 403


def test_invalid_dataset_path_param_returns_400(console_main):
    client = TestClient(console_main.app)
    client.cookies.set("mod_session", "legacy-session-token")

    response = client.get("/api/data/bad-name/options?columns=cliente")

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid dataset name"


def test_assistant_chat_without_auth_returns_401(console_main):
    client = TestClient(console_main.app)

    response = client.post("/assistant/chat", json={"message": "hello", "history": []})

    assert response.status_code == 401


def test_assistant_chat_with_valid_jwt_returns_200(console_main):
    client = TestClient(console_main.app)
    token = create_access_token({"sub": "42", "email": "analyst@example.com", "role": "analyst"})

    response = client.post(
        "/assistant/chat",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "hello", "history": []},
    )

    assert response.status_code == 200
    assert response.json()["reply"] == "echo:hello"


def test_jobs_without_auth_returns_401(console_main):
    client = TestClient(console_main.app)

    response = client.get("/jobs")

    assert response.status_code == 401


def test_jobs_with_valid_jwt_returns_200(console_main):
    client = TestClient(console_main.app)
    token = create_access_token({"sub": "42", "email": "analyst@example.com", "role": "analyst"})

    response = client.get("/jobs", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["jobs"][0]["id"] == "job-1"


def test_tokens_summary_rejects_viewer(console_main):
    client = TestClient(console_main.app)
    console_main._auth.user["role"] = "user"
    console_main._auth.workspace_rows[0]["workspace_role"] = "viewer"
    token = create_access_token({"sub": "42", "email": "analyst@example.com", "role": "user"})

    response = client.get("/tokens/summary", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403


def test_tokens_summary_allows_admin(console_main):
    client = TestClient(console_main.app)
    console_main._auth.user["role"] = "user"
    console_main._auth.workspace_rows[0]["workspace_role"] = "admin"
    token = create_access_token({"sub": "42", "email": "analyst@example.com", "role": "user"})

    response = client.get("/tokens/summary", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["total_tokens"] == 123


def test_api_users_rejects_viewer(console_main):
    client = TestClient(console_main.app)
    console_main._auth.user["role"] = "user"
    console_main._auth.workspace_rows[0]["workspace_role"] = "viewer"
    token = create_access_token({"sub": "42", "email": "analyst@example.com", "role": "user"})

    response = client.get("/api/users", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403


def test_api_users_allows_admin(console_main):
    client = TestClient(console_main.app)
    console_main._auth.user["role"] = "user"
    console_main._auth.workspace_rows[0]["workspace_role"] = "admin"
    token = create_access_token({"sub": "42", "email": "analyst@example.com", "role": "user"})

    response = client.get("/api/users", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["users"][0]["id"] == 42


def test_refresh_issues_new_access_token_and_rotates_refresh(console_main):
    client = TestClient(console_main.app)
    client.cookies.set("refresh_token", "valid-refresh-token")

    response = client.post("/auth/refresh")

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    decoded = decode_access_token(body["access_token"])
    assert decoded["sub"] == "42"
    assert "valid-refresh-token" in console_main._auth.revoked_refresh_tokens
    assert "refresh_token=refresh-token-1" in response.headers.get("set-cookie", "")


def test_logout_revokes_refresh_token(console_main):
    client = TestClient(console_main.app)
    client.cookies.set("mod_session", "legacy-session-token")
    client.cookies.set("refresh_token", "valid-refresh-token")

    response = client.post("/auth/logout")

    assert response.status_code == 200
    assert "valid-refresh-token" in console_main._auth.revoked_refresh_tokens
    set_cookie = response.headers.get("set-cookie", "")
    assert "mod_session=" in set_cookie
    assert "refresh_token=" in set_cookie
