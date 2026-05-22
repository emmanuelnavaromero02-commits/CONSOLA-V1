"""Sprint Phase-0 — close P0/P1 audit findings on the marketplace.admin
permission and on `marketplace.activate_product`.

These are pure unit tests on the permission registry + a source-level
assertion on activate_product. They do not touch the database; they only
verify that the audit findings cannot regress.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import permissions

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# P0 #5 — marketplace.admin must NOT be a hardcoded global-role bypass.
# It must flow through ROLE_PERMISSIONS exactly like every other permission.
# ---------------------------------------------------------------------------

def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_marketplace_admin_has_no_hardcoded_bypass_in_has_permission():
    """The legacy special-case in `has_permission` must be gone."""
    src = _source("app/services/permissions.py")
    # The previous bypass returned True directly from canonical_role(...). It
    # is reintroduced if anyone special-cases the permission inside
    # has_permission again. Defend against that.
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
    """workspace_role=admin is NOT a global admin and must not grant
    marketplace.admin. This is the golden SaaS rule."""
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


# ---------------------------------------------------------------------------
# P0 #6 — activate_product must re-validate internal_only/status before
# approving an existing installation.
# ---------------------------------------------------------------------------

def test_activate_product_revalidates_internal_only():
    """activate_product() must call the shared `_assert_product_activatable`
    guard (which reads marketplace_products.status and metadata->>
    'internal_only') BEFORE forwarding to approve_installation().
    """
    src = _source("app/services/marketplace_service.py")
    activate_section = src.split("async def activate_product", 1)[1]
    # Stop at the next top-level async def so we only inspect activate_product.
    activate_section = activate_section.split("\nasync def ", 1)[0]
    assert "_assert_product_activatable" in activate_section, (
        "activate_product must call _assert_product_activatable (shared "
        "with reactivate_installation) so the same internal_only / status "
        "guard runs for both paths."
    )


def test_activate_product_validation_runs_before_approve_call():
    """Source-level ordering check: the internal_only / status guard must
    come BEFORE the call to approve_installation(...) inside activate_product.
    """
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


# ---------------------------------------------------------------------------
# P0 — reactivate_installation MUST re-validate internal_only / status.
# Round-1 audit found the same bypass that activate_product had: an admin
# could pause a cartridge, the product could later be marked internal_only,
# and reactivate would silently re-enable it. The fix moved the guard into
# a shared helper `_assert_product_activatable`. Pin that contract.
# ---------------------------------------------------------------------------

def test_reactivate_installation_revalidates_product_state():
    """CISO Round-3 hardening: reactivate must run the product-activatable
    check INSIDE the state-change transaction. The simplest contract pin
    is to confirm reactivate_installation forwards `assert_product_
    activatable=True` to `_set_installation_state`, which is where the
    transactional check now lives."""
    src = _source("app/services/marketplace_service.py")
    react_section = src.split("async def reactivate_installation", 1)[1]
    react_section = react_section.split("\nasync def ", 1)[0]
    assert "assert_product_activatable=True" in react_section, (
        "reactivate_installation must request the in-transaction product "
        "guard by passing assert_product_activatable=True to "
        "_set_installation_state."
    )


def test_approve_installation_requests_in_transaction_product_check():
    """activate_product reaches approve_installation, which in turn runs
    `_set_installation_state` with the in-transaction product guard."""
    src = _source("app/services/marketplace_service.py")
    approve_section = src.split("async def approve_installation", 1)[1]
    approve_section = approve_section.split("\nasync def ", 1)[0]
    assert "assert_product_activatable=True" in approve_section, (
        "approve_installation must opt into the in-transaction product "
        "guard so concurrent admins cannot flip internal_only after the "
        "external pre-check has run."
    )


def test_set_installation_state_applies_product_guard_under_for_update():
    """Source-level contract: the in-transaction guard MUST live inside the
    `FOR UPDATE` row-locked block of `_set_installation_state`. Otherwise
    the TOCTOU window the CISO flagged in Round 3 stays open."""
    src = _source("app/services/marketplace_service.py")
    state_section = src.split("async def _set_installation_state", 1)[1]
    state_section = state_section.split("\nasync def ", 1)[0]
    assert "assert_product_activatable" in state_section, "param must exist"
    assert "FOR UPDATE OF ci" in state_section, "row lock must remain"
    # The product check must appear AFTER the FOR UPDATE row fetch and
    # BEFORE the UPDATE statements.
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
    # The helper must reject non-active status AND internal_only.
    assert "internal_only" in helper_section
    assert "p.status" in helper_section or "status = 'active'" in helper_section
