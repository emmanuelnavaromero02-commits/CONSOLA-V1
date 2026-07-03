from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

from app.domains.apps.scope import (
    active_scoped_connection_cartridges,
    installed_scoped_app_cartridges,
    normalize_candidate_cartridges,
    resolve_scoped_config_cartridge,
    resolve_scoped_operation_cartridge,
    scope_catalog_cartridge_arg,
    workspace_scope_for_apps_filter,
)


def _noop_user(_user):
    return None


def _noop_cartridge(_user, _cartridge):
    return None


def test_normalize_candidate_cartridges_removes_empty_values():
    assert normalize_candidate_cartridges({" sap_successfactors ", "", "hubspot"}) == {
        "sap_successfactors",
        "hubspot",
    }


def test_resolve_scoped_operation_prefers_active_fallback():
    cartridge, active = resolve_scoped_operation_cartridge(
        {},
        None,
        active={"hubspot", "sap_successfactors"},
        allowed=None,
        fallback="sap_successfactors",
        candidates={"hubspot", "sap_successfactors"},
        require_workspace_scope=_noop_user,
        require_cartridge_visible=_noop_cartridge,
    )

    assert cartridge == "sap_successfactors"
    assert active == {"hubspot", "sap_successfactors"}


def test_resolve_scoped_operation_rejects_inactive_requested_cartridge():
    with pytest.raises(HTTPException) as exc:
        resolve_scoped_operation_cartridge(
            {},
            "replicon",
            active={"sap_successfactors"},
            allowed=None,
            fallback="sap_successfactors",
            candidates={"replicon", "sap_successfactors"},
            require_workspace_scope=_noop_user,
            require_cartridge_visible=_noop_cartridge,
        )

    assert exc.value.status_code == 403
    assert "not active" in str(exc.value.detail)


def test_resolve_scoped_config_uses_visible_entitlements_without_vault_connection():
    assert (
        resolve_scoped_config_cartridge(
            {},
            None,
            visible={"hubspot"},
            is_workspace_scoped=True,
            fallback="sap_successfactors",
            candidates={"hubspot", "sap_successfactors"},
            require_cartridge_visible=_noop_cartridge,
        )
        == "hubspot"
    )


def test_scope_catalog_cartridge_arg_uses_allowed_when_no_active_connection():
    assert (
        scope_catalog_cartridge_arg(
            {},
            None,
            active=set(),
            allowed={"sap_successfactors"},
            candidates={"sap_successfactors", "hubspot"},
            require_workspace_scope=_noop_user,
            require_cartridge_visible=_noop_cartridge,
    )
        == "sap_successfactors"
    )


class _Pool:
    async def fetchval(self, *_args):
        return "tenant-from-db"

    async def fetchrow(self, *_args):
        return {"tenant_id": "tenant-gold", "workspace_id": "workspace-gold"}


async def _memberships(_user_id: int):
    return [{"tenant_id": "tenant-1", "workspace_id": "workspace-1"}]


async def _pool():
    return _Pool()


def _context(_user):
    return {}


def _scoped_user(user, tenant_id, workspace_id):
    return {
        **(user or {}),
        "active_tenant_id": tenant_id,
        "active_workspace_id": workspace_id,
    }


class _Logger:
    def debug(self, *_args, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_workspace_scope_for_apps_filter_uses_user_membership():
    assert await workspace_scope_for_apps_filter(
        {"id": 1},
        context_factory=_context,
        workspace_memberships=_memberships,
        get_db_pool=_pool,
        role_admin="admin",
        logger=_Logger(),
    ) == ("tenant-1", "workspace-1")


@pytest.mark.asyncio
async def test_workspace_scope_for_apps_filter_resolves_workspace_tenant_from_db():
    assert await workspace_scope_for_apps_filter(
        {"workspace_id": "workspace-1"},
        context_factory=_context,
        workspace_memberships=_memberships,
        get_db_pool=_pool,
        role_admin="admin",
        logger=_Logger(),
    ) == ("tenant-from-db", "workspace-1")


@pytest.mark.asyncio
async def test_workspace_scope_for_apps_filter_owner_falls_back_to_gold_workspace():
    assert await workspace_scope_for_apps_filter(
        {"role": "owner"},
        context_factory=_context,
        workspace_memberships=_memberships,
        get_db_pool=_pool,
        role_admin="admin",
        logger=_Logger(),
    ) == ("tenant-gold", "workspace-gold")


class _InstallConn:
    def __init__(self):
        self.fetch_args = None

    async def fetch(self, _sql, *args):
        self.fetch_args = args
        return [
            {"cartridge_id": "sap_successfactors"},
            {"cartridge_id": "hubspot"},
        ]


@pytest.mark.asyncio
async def test_installed_scoped_app_cartridges_filters_to_visible_scope():
    conn = _InstallConn()

    async def _scope(_user):
        return "tenant-1", "workspace-1"

    async def _install_pool():
        return object()

    @asynccontextmanager
    async def _scoped_db(_pool, scoped_user):
        assert scoped_user["active_tenant_id"] == "tenant-1"
        assert scoped_user["active_workspace_id"] == "workspace-1"
        yield conn, None, None

    result = await installed_scoped_app_cartridges(
        {"id": 1},
        {"sap_successfactors", "hubspot", ""},
        scope_resolver=_scope,
        get_db_pool=_install_pool,
        scoped_db_for_user=_scoped_db,
        scoped_user_factory=_scoped_user,
        context_visible_cartridges=lambda _user: {"sap_successfactors"},
    )

    assert result == {"sap_successfactors"}
    assert conn.fetch_args == (
        "tenant-1",
        "workspace-1",
        ["hubspot", "sap_successfactors"],
    )


@pytest.mark.asyncio
async def test_installed_scoped_app_cartridges_returns_empty_without_scope():
    async def _scope(_user):
        return "", ""

    async def _unexpected_pool():
        raise AssertionError("pool should not be requested without scope")

    @asynccontextmanager
    async def _scoped_db(_pool, _scoped_user):
        yield None, None, None

    assert (
        await installed_scoped_app_cartridges(
            {"id": 1},
            {"sap_successfactors"},
            scope_resolver=_scope,
            get_db_pool=_unexpected_pool,
            scoped_db_for_user=_scoped_db,
            scoped_user_factory=_scoped_user,
            context_visible_cartridges=lambda _user: None,
        )
        == set()
    )


class _VaultResponse:
    def __init__(self, status_code, payload=None, *, invalid_json=False):
        self.status_code = status_code
        self._payload = payload
        self._invalid_json = invalid_json

    def json(self):
        if self._invalid_json:
            raise ValueError("invalid json")
        return self._payload


class _VaultClient:
    requests: list[tuple[dict, str]] = []

    def __init__(self, *, headers, timeout):
        self.headers = headers
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def get(self, url):
        self.requests.append((self.headers, url))
        if url.endswith("/sap_successfactors"):
            return _VaultResponse(200, {"connections": [{"id": "sf"}]})
        if url.endswith("/hubspot"):
            return _VaultResponse(404, {})
        if url.endswith("/salesforce"):
            return _VaultResponse(200, {"connections": []})
        if url.endswith("/replicon"):
            return _VaultResponse(200, {}, invalid_json=True)
        raise RuntimeError("vault unavailable")


@pytest.mark.asyncio
async def test_active_scoped_connection_cartridges_reads_vault_connections():
    _VaultClient.requests = []

    async def _scope(_user):
        return "tenant-1", "workspace-1"

    result = await active_scoped_connection_cartridges(
        {"id": 1},
        {"sap_successfactors", "hubspot", "salesforce", "replicon", "bad"},
        scope_resolver=_scope,
        scoped_user_factory=_scoped_user,
        vault_url="http://vault",
        vault_headers_for_user=lambda user: {
            "x-tenant": user["active_tenant_id"],
            "x-workspace": user["active_workspace_id"],
        },
        http_client_factory=_VaultClient,
    )

    assert result == {"sap_successfactors"}
    assert _VaultClient.requests
    first_headers, first_url = _VaultClient.requests[0]
    assert first_headers == {"x-tenant": "tenant-1", "x-workspace": "workspace-1"}
    assert first_url.startswith("http://vault/connections/")


@pytest.mark.asyncio
async def test_active_scoped_connection_cartridges_returns_empty_without_candidates():
    async def _scope(_user):
        raise AssertionError("scope should not be resolved without candidates")

    assert (
        await active_scoped_connection_cartridges(
            {"id": 1},
            set(),
            scope_resolver=_scope,
            scoped_user_factory=_scoped_user,
            vault_url="http://vault",
            vault_headers_for_user=lambda _user: {},
            http_client_factory=_VaultClient,
        )
        == set()
    )
