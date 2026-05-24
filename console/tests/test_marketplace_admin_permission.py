"""P3 — marketplace admin is enforced by the marketplace.admin PERMISSION,
not by a hardcoded global role.

Before P3 the admin marketplace routes gated on
``require_global_any_role("owner", "super_admin", admin)`` and the service
re-checked ``user.get("role") in ADMIN_ROLES``. The permission existed in the
registry and was surfaced to the UI (``can_admin_marketplace``) but was never
the thing the backend actually enforced — so a custom role granted
``marketplace.admin`` saw the buttons yet got 403 from the API.

These tests mount the REAL ``require_permission("marketplace.admin")`` guard
(the same dependency the production routes now use) behind a tiny app and
assert that access follows the effective-permission set:

  * owner / admin                       -> 2xx (regression: still allowed)
  * a custom role carrying the perm     -> 2xx (the new contract)
  * workspace_admin (lacks the perm)    -> 403 (the golden SaaS rule holds)
  * viewer                              -> 403

We don't import console/app/main.py (heavy DB/CORS/proxy startup); we mount the
production guard on a stub route so the test focuses on the authz wiring.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app.services import permissions


def _build_client(user: dict | None) -> TestClient:
    """Mount one route guarded by the production marketplace.admin guard.

    A tiny middleware seeds ``request.state.user`` exactly like the console's
    auth_middleware does in production, because ``require_permission`` reads the
    caller from there."""
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
    """The new contract: ANY role whose effective permissions include
    marketplace.admin is allowed — enforcement is permission-based, not a
    role-name allowlist. The role must be registered (canonical_role maps
    unknown roles to __unknown__) and granted the permission."""
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
    # sanity: the custom role is NOT one of the legacy global-admin roles
    user = {"id": 3, "email": "ops@ex.com", "role": "marketplace_operator"}
    assert permissions.has_permission(user, "marketplace.admin")
    r = _build_client(user).get("/api/admin/installations")
    assert r.status_code == 200


def test_workspace_admin_cannot_admin_marketplace():
    """The golden SaaS rule: a workspace-scoped admin must NOT be able to
    administer the global marketplace."""
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
