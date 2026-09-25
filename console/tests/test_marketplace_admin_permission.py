from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app.services import permissions


def _build_client(user: dict | None) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    @app.get(
        "/api/admin/installations",
        dependencies=[Depends(permissions.require_permission("marketplace.admin"))],
    )
    async def _installations():
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=True)


def test_owner_can_admin_marketplace():
    r = _build_client({"id": 1, "email": "owner@ex.com", "role": "owner"}).get(
        "/api/admin/installations"
    )
    assert r.status_code == 200


def test_global_admin_can_admin_marketplace():
    r = _build_client({"id": 2, "email": "admin@ex.com", "role": "admin"}).get(
        "/api/admin/installations"
    )
    assert r.status_code == 200


def test_custom_role_with_permission_can_admin_marketplace(monkeypatch):
    monkeypatch.setitem(
        permissions.ROLE_DEFINITIONS,
        "marketplace_operator",
        {"label": "Marketplace Operator", "assignable": True, "builtin": False},
    )
    monkeypatch.setitem(
        permissions.ROLE_PERMISSIONS,
        "marketplace_operator",
        {"marketplace.admin", "marketplace.read"},
    )
    user = {"id": 3, "email": "ops@ex.com", "role": "marketplace_operator"}
    assert permissions.has_permission(user, "marketplace.admin")
    r = _build_client(user).get("/api/admin/installations")
    assert r.status_code == 200


def test_workspace_admin_cannot_admin_marketplace():
    user = {
        "id": 4,
        "email": "ws-admin@ex.com",
        "role": "user",
        "workspace_role": "workspace_admin",
    }
    assert not permissions.has_permission(user, "marketplace.admin")
    r = _build_client(user).get("/api/admin/installations")
    assert r.status_code == 403


def test_viewer_cannot_admin_marketplace():
    r = _build_client({"id": 5, "email": "v@ex.com", "role": "viewer"}).get(
        "/api/admin/installations"
    )
    assert r.status_code == 403


def test_anonymous_is_rejected():
    r = _build_client(None).get("/api/admin/installations")
    assert r.status_code in (401, 403)
