from __future__ import annotations

import importlib
import sys
import types

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.services.jwt_auth import create_access_token


JWT_SECRET = "test_jwt_secret_key_with_more_than_32_chars"


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


@pytest.fixture()
def dependency_app(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15")

    auth_stub = _module()
    auth_stub.COOKIE_NAME = "mod_session"
    auth_stub.user = {
        "id": 42,
        "email": "analyst@example.com",
        "name": "Test Analyst",
        "role": "analyst",
        "is_active": True,
        "must_change_password": False,
    }
    auth_stub.workspace_rows = [
        {
            "workspace_id": "11111111-1111-1111-1111-111111111111",
            "workspace_name": "Main Workspace",
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "tenant_name": "Default Tenant",
            "workspace_role": "analyst",
        },
        {
            "workspace_id": "22222222-2222-2222-2222-222222222222",
            "workspace_name": "Finance Workspace",
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "tenant_name": "Default Tenant",
            "workspace_role": "viewer",
        },
    ]

    async def get_user_by_id(user_id):
        if user_id == auth_stub.user["id"]:
            return dict(auth_stub.user)
        return None

    async def get_session_user(token):
        if token == "legacy-session-token":
            return dict(auth_stub.user)
        return None

    class FakePool:
        async def fetch(self, query, user_id):
            return [dict(row) for row in auth_stub.workspace_rows]

    async def pool():
        return FakePool()

    auth_stub.get_user_by_id = get_user_by_id
    auth_stub.get_session_user = get_session_user
    auth_stub.pool = pool

    services_pkg = importlib.import_module("app.services")
    monkeypatch.setattr(services_pkg, "auth", auth_stub)
    monkeypatch.setitem(sys.modules, "app.services.auth", auth_stub)
    sys.modules.pop("app.dependencies", None)
    deps = importlib.import_module("app.dependencies")

    app = FastAPI()

    @app.get("/me")
    async def me(user: dict = Depends(deps.get_current_user)):
        return {"user": user}

    @app.get("/admin")
    async def admin(user: dict = Depends(deps.require_role(deps.ROLE_ADMIN))):
        return {"user": user}

    yield app, auth_stub
    sys.modules.pop("app.dependencies", None)


def _token(role: str = "analyst") -> str:
    return create_access_token({
        "sub": "42",
        "email": "analyst@example.com",
        "role": role,
    })


def test_get_current_user_with_valid_jwt_returns_user(dependency_app):
    app, _ = dependency_app
    client = TestClient(app)

    response = client.get("/me", headers={"Authorization": f"Bearer {_token()}"})

    assert response.status_code == 200
    assert response.json()["user"]["id"] == 42
    assert response.json()["user"]["email"] == "analyst@example.com"
    assert response.json()["user"]["active_workspace_id"] == "11111111-1111-1111-1111-111111111111"
    assert response.json()["user"]["active_tenant_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert response.json()["user"]["workspace_role"] == "analyst"
    assert len(response.json()["user"]["workspaces"]) == 2


def test_get_current_user_with_valid_jwt_and_workspace_header_returns_requested_workspace(dependency_app):
    app, _ = dependency_app
    client = TestClient(app)

    response = client.get(
        "/me",
        headers={
            "Authorization": f"Bearer {_token()}",
            "X-Workspace-Id": "22222222-2222-2222-2222-222222222222",
        },
    )

    assert response.status_code == 200
    assert response.json()["user"]["active_workspace_id"] == "22222222-2222-2222-2222-222222222222"
    assert response.json()["user"]["workspace_role"] == "viewer"


def test_get_current_user_with_unassigned_workspace_header_returns_403(dependency_app):
    app, _ = dependency_app
    client = TestClient(app)

    response = client.get(
        "/me",
        headers={
            "Authorization": f"Bearer {_token()}",
            "X-Workspace-Id": "99999999-9999-9999-9999-999999999999",
        },
    )

    assert response.status_code == 403


def test_get_current_user_without_workspace_assignment_returns_403(dependency_app):
    app, auth_stub = dependency_app
    auth_stub.workspace_rows = []
    client = TestClient(app)

    response = client.get("/me", headers={"Authorization": f"Bearer {_token()}"})

    assert response.status_code == 403
    assert response.json()["detail"] == "user has no assigned workspace"


def test_get_current_user_with_invalid_jwt_and_no_legacy_session_returns_401(dependency_app):
    app, _ = dependency_app
    client = TestClient(app)

    response = client.get("/me", headers={"Authorization": "Bearer invalid-token"})

    assert response.status_code == 401


def test_get_current_user_with_legacy_cookie_still_works(dependency_app):
    app, _ = dependency_app
    client = TestClient(app)
    client.cookies.set("mod_session", "legacy-session-token")

    response = client.get("/me")

    assert response.status_code == 200
    assert response.json()["user"]["id"] == 42
    assert response.json()["user"]["active_workspace_id"] == "11111111-1111-1111-1111-111111111111"


def test_require_role_admin_allows_admin(dependency_app):
    app, auth_stub = dependency_app
    auth_stub.user["role"] = "admin"
    auth_stub.workspace_rows[0]["workspace_role"] = "admin"
    client = TestClient(app)

    response = client.get("/admin", headers={"Authorization": f"Bearer {_token('admin')}"})

    assert response.status_code == 200
    assert response.json()["user"]["workspace_role"] == "admin"


def test_require_role_admin_rejects_viewer(dependency_app):
    app, auth_stub = dependency_app
    auth_stub.user["role"] = "viewer"
    client = TestClient(app)

    response = client.get("/admin", headers={"Authorization": f"Bearer {_token('viewer')}"})

    assert response.status_code == 403
