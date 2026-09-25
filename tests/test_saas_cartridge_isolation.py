from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from starlette.requests import Request

from app import dependencies as console_dependencies
from app.services.mcp_registry import _require_cartridge_scope
from app.services.security_context import build_security_context


ROOT = Path(__file__).resolve().parents[1]


class Row(dict):
    def __getattr__(self, key: str):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


class TenantAccessPool:
    def __init__(self) -> None:
        self.entitlements = [
            {"tenant_id": "tenant-a", "workspace_id": "workspace-a", "cartridge_id": "replicon", "status": "active"},
            {"tenant_id": "tenant-b", "workspace_id": "workspace-b", "cartridge_id": "sap_successfactors", "status": "active"},
        ]
        self.installations = [
            {"tenant_id": "tenant-a", "workspace_id": "workspace-a", "cartridge_id": "replicon", "status": "ready"},
            {"tenant_id": "tenant-b", "workspace_id": "workspace-b", "cartridge_id": "sap_successfactors", "status": "ready"},
        ]
        self.denies = {
            ("tenant-a", "workspace-a", "replicon", 102),
        }

    async def fetchval(self, query: str, *args):
        if "tenant_entitlements" in query:
            return "public.tenant_entitlements"
        if "user_cartridge_overrides" in query:
            return "public.user_cartridge_overrides"
        return None

    async def fetch(self, query: str, *args):
        workspace_id = args[0]
        user_id = args[1] if len(args) > 1 else None
        cartridges = []
        for entitlement in self.entitlements:
            if entitlement["workspace_id"] != workspace_id:
                continue
            if entitlement["status"] != "active":
                continue
            cartridge_id = entitlement["cartridge_id"]
            ready = any(
                item["tenant_id"] == entitlement["tenant_id"]
                and item["workspace_id"] == workspace_id
                and item["cartridge_id"] == cartridge_id
                and item["status"] == "ready"
                for item in self.installations
            )
            if not ready:
                continue
            denied = (
                entitlement["tenant_id"],
                workspace_id,
                cartridge_id,
                user_id,
            ) in self.denies
            if denied:
                continue
            cartridges.append(cartridge_id)
        return [Row(cartridge=cartridge) for cartridge in sorted(set(cartridges))]

    def set_entitlement_status(self, tenant_id: str, workspace_id: str, cartridge_id: str, status: str) -> None:
        for entitlement in self.entitlements:
            if (
                entitlement["tenant_id"] == tenant_id
                and entitlement["workspace_id"] == workspace_id
                and entitlement["cartridge_id"] == cartridge_id
            ):
                entitlement["status"] = status

    def set_installation_status(self, tenant_id: str, workspace_id: str, cartridge_id: str, status: str) -> None:
        for installation in self.installations:
            if (
                installation["tenant_id"] == tenant_id
                and installation["workspace_id"] == workspace_id
                and installation["cartridge_id"] == cartridge_id
            ):
                installation["status"] = status


def _load_workspace_session_module():
    path = ROOT / "workspace/app/services/session.py"
    spec = importlib.util.spec_from_file_location("workspace_session_isolation_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _request_with_workspace_context(
    *, header: str | None = None, cookie: str | None = None
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if header is not None:
        headers.append((b"x-workspace-id", header.encode("utf-8")))
    if cookie is not None:
        headers.append(
            (b"cookie", f"omega_active_workspace_id={cookie}".encode("utf-8"))
        )
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/studio/cartridges",
            "headers": headers,
        }
    )


def test_legacy_console_reads_active_workspace_cookie_and_header_wins():
    cookie_request = _request_with_workspace_context(cookie="workspace-b")
    assert console_dependencies.requested_workspace_id_from_request(cookie_request) == "workspace-b"

    header_request = _request_with_workspace_context(header="workspace-a", cookie="workspace-b")
    assert console_dependencies.requested_workspace_id_from_request(header_request) == "workspace-a"


@pytest.mark.asyncio
async def test_two_tenants_and_user_deny_stay_isolated_in_console_and_workspace(monkeypatch):
    pool = TenantAccessPool()

    async def fake_console_pool():
        return pool

    monkeypatch.setattr(console_dependencies._auth, "pool", fake_console_pool)
    workspace_session = _load_workspace_session_module()

    assert await console_dependencies._workspace_cartridges("workspace-a", user_id=101) == ["replicon"]
    assert await workspace_session._workspace_cartridges(pool, "workspace-a", user_id=101) == ["replicon"]

    assert await console_dependencies._workspace_cartridges("workspace-a", user_id=102) == []
    assert await workspace_session._workspace_cartridges(pool, "workspace-a", user_id=102) == []

    assert await console_dependencies._workspace_cartridges("workspace-b", user_id=201) == ["sap_successfactors"]
    assert "replicon" not in await workspace_session._workspace_cartridges(pool, "workspace-b", user_id=201)

    pool.set_entitlement_status("tenant-a", "workspace-a", "replicon", "revoked")
    assert await console_dependencies._workspace_cartridges("workspace-a", user_id=101) == []
    assert await workspace_session._workspace_cartridges(pool, "workspace-a", user_id=101) == []

    pool.set_entitlement_status("tenant-a", "workspace-a", "replicon", "active")
    pool.set_installation_status("tenant-a", "workspace-a", "replicon", "ready")
    assert await console_dependencies._workspace_cartridges("workspace-a", user_id=101) == ["replicon"]
    assert await console_dependencies._workspace_cartridges("workspace-a", user_id=102) == []


def test_copilot_mcp_scope_follows_workspace_allowlist_not_admin_magic():
    pedro = {
        "id": 101,
        "email": "pedro@cliente-a.test",
        "role": "user",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "allowed_cartridges": ["replicon"],
    }
    maria = {
        "id": 102,
        "email": "maria@cliente-a.test",
        "role": "admin",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "allowed_cartridges": [],
    }
    platform_admin = {
        "id": 1,
        "email": "owner@omega.test",
        "role": "admin",
    }

    pedro_ctx = build_security_context(pedro)
    _require_cartridge_scope(pedro_ctx, "replicon")
    with pytest.raises(PermissionError):
        _require_cartridge_scope(pedro_ctx, "sap_successfactors")

    maria_ctx = build_security_context(maria)
    assert maria_ctx["allowed_cartridges"] == []
    with pytest.raises(PermissionError):
        _require_cartridge_scope(maria_ctx, "replicon")

    platform_ctx = build_security_context(platform_admin)
    assert platform_ctx["allowed_cartridges"] == ["*"]
    _require_cartridge_scope(platform_ctx, "replicon")
    _require_cartridge_scope(platform_ctx, "sap_successfactors")
