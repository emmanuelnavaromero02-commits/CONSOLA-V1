from __future__ import annotations

from app.domains.studio.access import cartridge_visible_for_context


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
