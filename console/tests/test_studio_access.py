from __future__ import annotations

from app.domains.studio.access import (
    cartridge_visible_for_context,
    has_studio_ops_write_role,
    studio_ops_role_name,
)


def test_cartridge_visible_for_context_allows_explicit_and_wildcard_access():
    assert cartridge_visible_for_context(
        {"allowed_cartridges": ["sap_successfactors"]},
        "sap_successfactors",
    )
    assert cartridge_visible_for_context({"allowed_cartridges": ["*"]}, "replicon")


def test_cartridge_visible_for_context_denies_unlisted_cartridge():
    assert not cartridge_visible_for_context(
        {"allowed_cartridges": ["sap_successfactors"]},
        "replicon",
    )
    assert not cartridge_visible_for_context({}, "sap_successfactors")


def test_studio_ops_role_name_prefers_workspace_role():
    assert (
        studio_ops_role_name({"role": "admin", "workspace_role": "analyst"})
        == "analyst"
    )
    assert studio_ops_role_name({"role": "viewer"}) == "viewer"
    assert studio_ops_role_name({}) == ""
    assert studio_ops_role_name(None) == ""


def test_studio_ops_write_role_is_global_only():
    assert has_studio_ops_write_role({"role": "owner"})
    assert has_studio_ops_write_role({"role": "super_admin"})
    assert has_studio_ops_write_role({"role": "admin"})
    assert not has_studio_ops_write_role(
        {"role": "user", "workspace_role": "admin"}
    )
    assert not has_studio_ops_write_role({"role": "analyst"})
