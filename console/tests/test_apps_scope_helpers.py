from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.apps.scope import (
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
