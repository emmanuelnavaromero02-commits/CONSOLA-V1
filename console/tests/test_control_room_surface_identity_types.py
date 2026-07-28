from __future__ import annotations

import pytest

from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.business_surface_identity import (
    BusinessSurfaceIdentity,
    resolve_business_surface_identity,
)
from control_room_surface_fixtures import business_item, snapshot


@pytest.mark.parametrize("invalid", ({"bad": "mapping"}, ["bad"], 42))
def test_non_string_domain_is_never_stringified(invalid: object) -> None:
    item = {
        "domain": invalid,
        "cartridge": "sap_hcm",
        "module_id": "people_overview",
    }

    assert resolve_business_surface_identity(item) is None


@pytest.mark.parametrize("invalid", ({"bad": "mapping"}, ["bad"], 42))
def test_non_string_structural_value_uses_valid_string_fallback(
    invalid: object,
) -> None:
    item = {
        "domain": invalid,
        "metadata": {"domain": "People"},
        "cartridge_id": invalid,
        "cartridge": "sap_hcm",
        "module_id": invalid,
        "source_dataset": "people_overview",
    }

    assert resolve_business_surface_identity(item) == BusinessSurfaceIdentity(
        domain="People",
        cartridge_id="sap_hcm",
        module_id="people_overview",
    )


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("domain", {"bad": "mapping"}),
        ("cartridge", ["bad"]),
        ("module_id", 42),
    ),
)
def test_item_without_complete_string_identity_produces_no_section(
    field: str,
    invalid: object,
) -> None:
    item = business_item(**{field: invalid})
    if field == "module_id":
        item["source_dataset"] = invalid

    assert build_business_experience(snapshot(items=(item,))).sections == []


def test_valid_structural_identity_is_used_without_being_published() -> None:
    item = business_item(
        domain="People Ops",
        cartridge="sap_hcm",
        module_id="people_overview",
    )

    section = build_business_experience(snapshot(items=(item,))).sections[0]

    assert section.domain == "People Ops"
    assert set(section.model_dump()) == {"title", "domain", "facts"}
    assert "sap_hcm" not in section.model_dump_json()
    assert "people_overview" not in section.model_dump_json()
