from __future__ import annotations

from pathlib import Path

import pytest

from app.services import permissions

ROOT = Path(__file__).resolve().parents[1]


def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_marketplace_admin_has_no_hardcoded_bypass_in_has_permission():
    src = _source("app/services/permissions.py")
    assert "if permission == \"marketplace.admin\":" not in src, (
        "marketplace.admin must flow through get_effective_permissions, "
        "never be hardcoded inside has_permission()."
    )


def test_global_admin_has_marketplace_admin():
    user = {"role": "admin"}
    assert permissions.has_permission(user, "marketplace.admin")


def test_global_owner_has_marketplace_admin():
    user = {"role": "owner"}
    assert permissions.has_permission(user, "marketplace.admin")


def test_super_admin_has_marketplace_admin():
    user = {"role": "super_admin"}
    assert permissions.has_permission(user, "marketplace.admin")


def test_workspace_admin_does_not_have_marketplace_admin():
    user = {"role": "user", "workspace_role": "admin"}
    assert not permissions.has_permission(user, "marketplace.admin")


def test_workspace_admin_canonical_does_not_have_marketplace_admin():
    user = {"role": "user", "workspace_role": "workspace_admin"}
    assert not permissions.has_permission(user, "marketplace.admin")


def test_analyst_does_not_have_marketplace_admin():
    user = {"role": "analyst"}
    assert not permissions.has_permission(user, "marketplace.admin")


def test_viewer_does_not_have_marketplace_admin():
    user = {"role": "viewer"}
    assert not permissions.has_permission(user, "marketplace.admin")


def test_workspace_user_does_not_have_marketplace_admin():
    user = {"role": "user", "workspace_role": "workspace_user"}
    assert not permissions.has_permission(user, "marketplace.admin")


def test_activate_product_revalidates_internal_only():
    src = _source("app/services/marketplace_service.py")
    activate_section = src.split("async def activate_product", 1)[1]
    activate_section = activate_section.split("\nasync def ", 1)[0]
    assert "_assert_product_activatable" in activate_section, (
        "activate_product must call _assert_product_activatable (shared "
        "with reactivate_installation) so the same internal_only / status "
        "guard runs for both paths."
    )


def test_activate_product_validation_runs_before_approve_call():
    src = _source("app/services/marketplace_service.py")
    activate_section = src.split("async def activate_product", 1)[1]
    activate_section = activate_section.split("\nasync def ", 1)[0]
    approve_idx = activate_section.find("approve_installation(")
    internal_idx = activate_section.find("_assert_product_activatable")
    assert approve_idx > 0 and internal_idx > 0, "expected both markers"
    assert internal_idx < approve_idx, (
        "_assert_product_activatable guard must execute BEFORE "
        "approve_installation is called"
    )


def test_reactivate_installation_revalidates_product_state():
    src = _source("app/services/marketplace_service.py")
    react_section = src.split("async def reactivate_installation", 1)[1]
    react_section = react_section.split("\nasync def ", 1)[0]
    assert "assert_product_activatable=True" in react_section, (
        "reactivate_installation must request the in-transaction product "
        "guard by passing assert_product_activatable=True to "
        "_set_installation_state."
    )


def test_approve_installation_requests_in_transaction_product_check():
    src = _source("app/services/marketplace_service.py")
    approve_section = src.split("async def approve_installation", 1)[1]
    approve_section = approve_section.split("\nasync def ", 1)[0]
    assert "assert_product_activatable=True" in approve_section, (
        "approve_installation must opt into the in-transaction product "
        "guard so concurrent admins cannot flip internal_only after the "
        "external pre-check has run."
    )


def test_set_installation_state_applies_product_guard_under_for_update():
    src = _source("app/services/marketplace_service.py")
    state_section = src.split("async def _set_installation_state", 1)[1]
    state_section = state_section.split("\nasync def ", 1)[0]
    assert "assert_product_activatable" in state_section, "param must exist"
    assert "FOR UPDATE OF ci" in state_section, "row lock must remain"
    lock_idx = state_section.find("FOR UPDATE OF ci")
    guard_idx = state_section.find("if assert_product_activatable:")
    update_idx = state_section.find("UPDATE marketplace_orders")
    assert lock_idx < guard_idx < update_idx, (
        "The product-activatable check must run between the row lock "
        "(FOR UPDATE OF ci) and the first UPDATE so it's atomic."
    )


def test_assert_product_activatable_helper_exists():
    src = _source("app/services/marketplace_service.py")
    assert "async def _assert_product_activatable" in src, (
        "Phase-0 fix added a shared `_assert_product_activatable(conn, "
        "cartridge_id)` helper. It remains for the non-transactional "
        "pre-check; the transactional guard now lives in "
        "_set_installation_state."
    )
    helper_section = src.split("async def _assert_product_activatable", 1)[1]
    helper_section = helper_section.split("\nasync def ", 1)[0]
    assert "internal_only" in helper_section
    assert "p.status" in helper_section or "status = 'active'" in helper_section
