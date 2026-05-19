"""Integration tests for /api/settings/* — auth + RBAC + happy paths.

Pattern: do NOT stub sys.modules (we need the real require_authenticated to
raise 401 when no session is present). Override the dependency with
app.dependency_overrides to inject test users.

Service-layer functions are patched on the imported module object so other
tests' sys.modules contamination cannot leak through.
"""
from __future__ import annotations

import os
import sys
import types
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Static-only test: stub asyncpg before importing the router/deps so import
# side effects don't try to connect to Postgres.
sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

os.environ.setdefault("INTERNAL_API_KEY", "test-key-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-key-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")

from app.dependencies import require_authenticated  # noqa: E402
from app.routers import settings as settings_router  # noqa: E402
from app.services import settings_service as _svc  # noqa: E402


_ADMIN = {"id": 1, "role": "admin", "email": "admin@example.com"}
_USER = {"id": 2, "role": "user", "email": "user@example.com"}
_CSRF = "unit-test-csrf"


def _make_app(user: dict | None = None) -> FastAPI:
    app = FastAPI()
    if user is not None:
        app.dependency_overrides[require_authenticated] = lambda: user
    app.include_router(settings_router.router)
    return app


def _csrf_headers(client: TestClient) -> dict[str, str]:
    client.cookies.set("csrf_token", _CSRF)
    return {"X-CSRF-Token": _CSRF}


def _setting_row(key, value, is_secret=False, category="test"):
    return {
        "key": key, "value": value, "is_secret": is_secret,
        "category": category, "description": "", "updated_at": datetime(2026, 1, 1),
    }


# ── 401 / 403 ────────────────────────────────────────────────────────────────

def test_get_settings_without_session_returns_401():
    client = TestClient(_make_app(user=None))
    response = client.get("/api/settings")
    assert response.status_code == 401


def test_get_settings_non_admin_returns_403():
    client = TestClient(_make_app(user=_USER))
    response = client.get("/api/settings")
    assert response.status_code == 403


# ── 200 GET list ─────────────────────────────────────────────────────────────

def test_get_settings_admin_returns_masked_list():
    client = TestClient(_make_app(user=_ADMIN))
    sample = [
        _setting_row("plain", "ok", is_secret=False),
        _setting_row("token", "***", is_secret=True),
    ]
    with patch.object(_svc, "list_settings", new=AsyncMock(return_value=sample)):
        response = client.get("/api/settings")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["settings"][0]["value"] == "ok"
    assert data["settings"][1]["value"] == "***"


def test_get_settings_passes_category_filter():
    client = TestClient(_make_app(user=_ADMIN))
    mock = AsyncMock(return_value=[])
    with patch.object(_svc, "list_settings", new=mock):
        client.get("/api/settings?category=integrations")
    mock.assert_awaited_once_with(category="integrations", include_secrets=False)


# ── 200 single GET ───────────────────────────────────────────────────────────

def test_get_setting_by_key_admin_returns_item():
    client = TestClient(_make_app(user=_ADMIN))
    with patch.object(_svc, "get_setting", new=AsyncMock(return_value=_setting_row("k", "v"))):
        response = client.get("/api/settings/k")
    assert response.status_code == 200
    assert response.json()["value"] == "v"


def test_get_setting_unknown_key_returns_404():
    client = TestClient(_make_app(user=_ADMIN))
    with patch.object(_svc, "get_setting", new=AsyncMock(return_value=None)):
        response = client.get("/api/settings/missing")
    assert response.status_code == 404


# ── 200 reveal ───────────────────────────────────────────────────────────────

def test_reveal_setting_admin_returns_real_value():
    client = TestClient(_make_app(user=_ADMIN))
    real = _setting_row("replicon_token", "real-bearer", is_secret=True)
    with patch.object(_svc, "reveal_setting", new=AsyncMock(return_value=real)) as mock:
        response = client.post("/api/settings/replicon_token/reveal", headers=_csrf_headers(client))
    assert response.status_code == 200
    assert response.json()["value"] == "real-bearer"
    # v1.41.0: settings router forwards ip + user_agent to settings_service
    # so the audit row records where the secret was revealed from.
    mock.assert_awaited_once_with(
        "replicon_token",
        user_id=1,
        user_email="admin@example.com",
        ip="testclient",
        user_agent="testclient",
    )


def test_reveal_setting_unknown_returns_404():
    client = TestClient(_make_app(user=_ADMIN))
    with patch.object(_svc, "reveal_setting", new=AsyncMock(return_value=None)):
        response = client.post("/api/settings/nope/reveal", headers=_csrf_headers(client))
    assert response.status_code == 404


# ── 200 PUT update ───────────────────────────────────────────────────────────

def test_update_setting_admin_persists():
    client = TestClient(_make_app(user=_ADMIN))
    updated = _setting_row("airflow_connection_mode", "real")
    with patch.object(_svc, "set_setting", new=AsyncMock(return_value=updated)) as mock:
        response = client.put(
            "/api/settings/airflow_connection_mode",
            json={"value": "real"},
            headers=_csrf_headers(client),
        )
    assert response.status_code == 200
    assert response.json()["value"] == "real"
    mock.assert_awaited_once_with(
        "airflow_connection_mode",
        "real",
        user_id=1,
        user_email="admin@example.com",
        ip="testclient",
        user_agent="testclient",
    )


def test_update_setting_missing_value_returns_400():
    client = TestClient(_make_app(user=_ADMIN))
    response = client.put("/api/settings/k", json={}, headers=_csrf_headers(client))
    assert response.status_code == 400


def test_update_setting_unknown_key_returns_404():
    client = TestClient(_make_app(user=_ADMIN))
    with patch.object(_svc, "set_setting", new=AsyncMock(side_effect=KeyError("nope"))):
        response = client.put("/api/settings/nope", json={"value": "x"}, headers=_csrf_headers(client))
    assert response.status_code == 404


# ── 200 rotate ───────────────────────────────────────────────────────────────

def test_rotate_secret_admin_returns_updated_row():
    client = TestClient(_make_app(user=_ADMIN))
    rotated = _setting_row("internal_api_key", "***", is_secret=True)
    with patch.object(_svc, "rotate_secret", new=AsyncMock(return_value=rotated)) as mock:
        response = client.post("/api/settings/internal_api_key/rotate", headers=_csrf_headers(client))
    assert response.status_code == 200
    mock.assert_awaited_once_with(
        "internal_api_key",
        user_id=1,
        user_email="admin@example.com",
        ip="testclient",
        user_agent="testclient",
    )


def test_rotate_secret_unknown_key_returns_404():
    client = TestClient(_make_app(user=_ADMIN))
    with patch.object(_svc, "rotate_secret", new=AsyncMock(side_effect=KeyError("nope"))):
        response = client.post("/api/settings/nope/rotate", headers=_csrf_headers(client))
    assert response.status_code == 404
