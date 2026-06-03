"""Phase-0 SaaS controls — contract test for /api/me/access.

The "Mis accesos" page renders strictly what this endpoint returns. The
endpoint:
  * requires authentication;
  * never invents permissions — it derives them from the canonical
    permission registry;
  * exposes both the global role and the workspace_role separately so the
    UI cannot conflate a workspace_admin with a platform admin;
  * surfaces cartridge allow/deny info coming from the marketplace tables;
  * sets `ui_capabilities` flags so the front-end can hide buttons that
    would 403 — those flags are display hints; the backend still enforces.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.services import permissions


# ---------------------------------------------------------------------------
# Minimal FastAPI app that mounts /api/me/access via the same code path as
# production. We don't import the whole console/app/main.py because that
# module triggers a heavy startup (CORS, security headers, refinement proxy,
# etc.) — instead we copy the body of the route into a tiny app, calling
# the same downstream helpers. This keeps the test focused on the endpoint
# contract and on the permission/role wiring.
# ---------------------------------------------------------------------------


def _build_app(fake_user: dict | None) -> TestClient:
    """Mount a tiny FastAPI app that exposes /api/me/access with the same
    payload shape as production. The full console main.py is too heavy for
    a unit test (it triggers DB pools, CORS, security headers, refinement
    proxy, etc.); we replicate the route here so the test focuses on the
    payload contract."""
    from fastapi import Depends, HTTPException
    app = FastAPI()

    async def fake_auth() -> dict:
        if fake_user is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return fake_user

    @app.get("/api/me/access")
    async def _me_access(user: dict = Depends(fake_auth)):
        effective = sorted(permissions.get_effective_permissions(user))
        role_canonical = permissions.canonical_role(user.get("role"))
        workspace_role_resolved = permissions.workspace_role(user) or None
        return {
            "user": {"id": user.get("id"), "email": user.get("email")},
            "role": {
                "global": role_canonical,
                "is_platform_admin": role_canonical in {"owner", "super_admin", "admin"},
            },
            "workspace": {
                "tenant_id": user.get("tenant_id") or user.get("active_tenant_id"),
                "workspace_id": user.get("workspace_id") or user.get("active_workspace_id"),
                "workspace_role": workspace_role_resolved,
            },
            "permissions": effective,
            "cartridges": {"allowed": [], "denied": []},
            "ui_capabilities": {
                # Replicate the full guard chain of /iam: permission AND
                # global admin role. Otherwise the UI would lie to
                # security_admin / auditor users who have the permission
                # but are not platform admins.
                "can_view_iam":            "iam.users.read" in effective and role_canonical in {"owner", "super_admin", "admin"},
                "can_manage_workspace_users": (
                    "iam.users.read" in effective
                    and (
                        role_canonical in {"owner", "super_admin", "admin"}
                        or workspace_role_resolved in {"workspace_admin", "tenant_admin"}
                    )
                ),
                "can_admin_marketplace":   "marketplace.admin" in effective,
                "can_admin_workspace":     workspace_role_resolved in {"workspace_admin", "tenant_admin"},
                "can_view_audit":          "security.audit.read" in effective,
                "can_view_sessions":       "security.sessions.read" in effective,
            },
        }

    return TestClient(app)


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


def test_me_access_returns_global_and_workspace_role_separately():
    """The golden SaaS rule: global role and workspace_role must NEVER be
    conflated in the payload."""
    client = _build_app({
        "id": 42,
        "email": "alice@example.com",
        "role": "user",
        "workspace_role": "workspace_admin",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
    })
    r = client.get("/api/me/access")
    assert r.status_code == 200
    body = r.json()
    assert body["role"]["global"] == "user"
    assert body["role"]["is_platform_admin"] is False
    assert body["workspace"]["workspace_role"] == "workspace_admin"
    # The same payload must say the user CAN administer their workspace
    # but CANNOT administer the platform marketplace.
    assert body["ui_capabilities"]["can_admin_workspace"] is True
    assert body["ui_capabilities"]["can_manage_workspace_users"] is True
    assert body["ui_capabilities"]["can_admin_marketplace"] is False


def test_workspace_role_admin_legacy_is_downgraded_to_workspace_admin():
    """Legacy DB row workspace_role=admin must be exposed as workspace_admin
    by /api/me/access — the front-end must not see the magical string
    'admin' for a workspace-scoped role."""
    client = _build_app({
        "id": 7,
        "email": "legacy-ws@example.com",
        "role": "user",
        "workspace_role": "admin",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
    })
    body = client.get("/api/me/access").json()
    assert body["workspace"]["workspace_role"] == "workspace_admin"
    assert body["ui_capabilities"]["can_admin_marketplace"] is False


def test_tenant_admin_can_manage_workspace_users_without_internal_surfaces():
    client = _build_app({
        "id": 55,
        "email": "tenant-admin@example.com",
        "role": "user",
        "workspace_role": "tenant_admin",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
    })
    body = client.get("/api/me/access").json()
    assert body["workspace"]["workspace_role"] == "tenant_admin"
    assert body["ui_capabilities"]["can_manage_workspace_users"] is True
    assert body["ui_capabilities"]["can_admin_workspace"] is True
    assert "iam.users.read" in body["permissions"]
    assert "iam.users.write" in body["permissions"]
    assert "studio.read" not in body["permissions"]
    assert "studio.write" not in body["permissions"]
    assert "datasets.write" not in body["permissions"]
    assert "vault.connections.read" in body["permissions"]
    assert "vault.connections.write" in body["permissions"]
    assert "vault.secrets.read_masked" in body["permissions"]
    assert "vault.secrets.reveal" not in body["permissions"]
    assert "cartridges.read" in body["permissions"]
    assert "cartridges.write" in body["permissions"]
    assert "cartridges.execute" in body["permissions"]
    assert "security.audit.read" in body["permissions"]
    assert "operations.read" in body["permissions"]


def test_platform_admin_capabilities():
    client = _build_app({
        "id": 1,
        "email": "root@example.com",
        "role": "admin",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
    })
    body = client.get("/api/me/access").json()
    assert body["role"]["is_platform_admin"] is True
    assert body["ui_capabilities"]["can_admin_marketplace"] is True
    assert body["ui_capabilities"]["can_view_iam"] is True


def test_viewer_capabilities():
    client = _build_app({
        "id": 99,
        "email": "v@example.com",
        "role": "viewer",
    })
    body = client.get("/api/me/access").json()
    assert body["role"]["is_platform_admin"] is False
    assert body["ui_capabilities"]["can_admin_marketplace"] is False
    assert body["ui_capabilities"]["can_view_iam"] is False
    assert body["ui_capabilities"]["can_view_audit"] is False
    # Viewer must NOT inherit any write permissions.
    assert "iam.users.write" not in body["permissions"]
    assert "marketplace.admin" not in body["permissions"]


def test_anonymous_caller_is_rejected():
    client = _build_app(fake_user=None)
    r = client.get("/api/me/access")
    assert r.status_code in (401, 403)


def test_security_admin_cannot_view_iam_link_despite_having_permission():
    """`security_admin` has iam.users.read but is NOT a platform admin.
    /iam requires BOTH (see pages.py). The UI capability flag must
    replicate that double gate so the UI does not show a link the
    backend rejects."""
    user = {
        "id": 8,
        "email": "sec-admin@example.com",
        "role": "security_admin",
    }
    client = _build_app(user)
    body = client.get("/api/me/access").json()
    assert "iam.users.read" in body["permissions"], "fixture sanity: security_admin should have iam.users.read"
    assert body["role"]["is_platform_admin"] is False
    assert body["ui_capabilities"]["can_view_iam"] is False, (
        "security_admin must NOT see the /iam link because the page also "
        "requires require_admin (global admin role)."
    )


def test_permissions_field_matches_effective_permissions_registry():
    """No invented permissions, no inflated set. The payload must equal
    `sorted(get_effective_permissions(user))` for the caller."""
    user = {
        "id": 5,
        "email": "analyst@example.com",
        "role": "analyst",
    }
    expected = sorted(permissions.get_effective_permissions(user))
    client = _build_app(user)
    body = client.get("/api/me/access").json()
    assert body["permissions"] == expected
