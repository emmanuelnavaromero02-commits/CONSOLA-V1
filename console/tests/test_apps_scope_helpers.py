from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.apps.scope import (
    normalize_candidate_cartridges,
    resolve_scoped_config_cartridge,
    resolve_scoped_operation_cartridge,
    scope_catalog_cartridge_arg,
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
