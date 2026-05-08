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

    async def get_user_by_id(user_id):
        if user_id == auth_stub.user["id"]:
            return dict(auth_stub.user)
        return None

    async def get_session_user(token):
        if token == "legacy-session-token":
            return dict(auth_stub.user)
        return None

    auth_stub.get_user_by_id = get_user_by_id
    auth_stub.get_session_user = get_session_user

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


def test_require_role_admin_allows_admin(dependency_app):
    app, auth_stub = dependency_app
    auth_stub.user["role"] = "admin"
    client = TestClient(app)

    response = client.get("/admin", headers={"Authorization": f"Bearer {_token('admin')}"})

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "admin"


def test_require_role_admin_rejects_viewer(dependency_app):
    app, auth_stub = dependency_app
    auth_stub.user["role"] = "viewer"
    client = TestClient(app)

    response = client.get("/admin", headers={"Authorization": f"Bearer {_token('viewer')}"})

    assert response.status_code == 403
