from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.services import permissions


def _build_app(fake_user: dict | None) -> TestClient:
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


def test_me_access_returns_global_and_workspace_role_separately():
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
    assert body["ui_capabilities"]["can_admin_workspace"] is True
    assert body["ui_capabilities"]["can_manage_workspace_users"] is True
    assert body["ui_capabilities"]["can_admin_marketplace"] is False


def test_workspace_role_admin_legacy_is_downgraded_to_workspace_admin():
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
    assert "pipelines.run" in body["permissions"]
    assert "pipelines.write" not in body["permissions"]
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
    assert "iam.users.write" not in body["permissions"]
    assert "marketplace.admin" not in body["permissions"]


def test_anonymous_caller_is_rejected():
    client = _build_app(fake_user=None)
    r = client.get("/api/me/access")
    assert r.status_code in (401, 403)


def test_security_admin_cannot_view_iam_link_despite_having_permission():
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
    user = {
        "id": 5,
        "email": "analyst@example.com",
        "role": "analyst",
    }
    expected = sorted(permissions.get_effective_permissions(user))
    client = _build_app(user)
    body = client.get("/api/me/access").json()
    assert body["permissions"] == expected
