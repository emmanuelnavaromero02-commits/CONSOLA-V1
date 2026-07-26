from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_explicit_action_binding import (
    ACTION_BINDING_TTL_SECONDS,
    ACTION_BINDING_VERSION,
    MAX_EXPLICIT_ACTION_BINDINGS,
    attach_explicit_action_binding,
    verified_explicit_action_bindings,
)
from app.services.control_room.business_template_contract import template_contract
from control_room_surface_fixtures import action_item, business_item


ISSUED_AT = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


def _clock(offset: int = 0):
    return lambda: ISSUED_AT + timedelta(seconds=offset)


def _external_item(
    *, cartridge: str, template_id: str, details: dict[str, str]
) -> dict:
    item = business_item(
        cartridge=cartridge,
        source_system=cartridge,
        details=details,
        metadata={
            "connection": {
                "base_url": f"https://{cartridge}.example.invalid",
                "default_writeback_path": "/api/v1/review",
            }
        },
    )
    return attach_explicit_action_binding(
        item,
        template_id=template_id,
        clock=_clock(),
    )


def _binding(item: dict) -> dict:
    return item["metadata"]["explicit_action_bindings"][0]


def test_v2_binding_has_bounded_server_window_and_two_digests():
    item = attach_explicit_action_binding(
        business_item(), template_id="request_owner_review", clock=_clock()
    )
    binding = _binding(item)

    assert binding["version"] == ACTION_BINDING_VERSION
    assert len(binding["execution_target_digest"]) == 64
    assert len(binding["template_contract_digest"]) == 64
    assert binding["issued_at"] == "2026-07-26T12:00:00Z"
    assert binding["expires_at"] == "2026-07-26T12:15:00Z"
    assert len(verified_explicit_action_bindings(item, clock=_clock(899))) == 1
    assert (
        verified_explicit_action_bindings(
            item, clock=_clock(ACTION_BINDING_TTL_SECONDS)
        )
        == ()
    )


def test_template_digest_covers_every_execution_semantic():
    contract = template_contract(ACTION_TEMPLATES["request_owner_review"])

    assert set(contract) == {
        "version",
        "template_id",
        "cartridge_id",
        "action_kind",
        "template_type",
        "risk_level",
        "mode_default",
        "surface",
        "capability",
        "writeback_contract",
        "requires_approval",
        "writeback_version",
    }


@pytest.mark.parametrize(
    ("template_id", "cartridge", "details", "field"),
    (
        ("prepare_hcm_access_review", "sap_hcm", {"pernr": "1001"}, "pernr"),
        (
            "prepare_successfactors_review",
            "sap_successfactors",
            {"user_id": "user-a"},
            "user_id",
        ),
        (
            "prepare_successfactors_recruiting_review",
            "sap_successfactors",
            {"requisition_id": "req-a"},
            "requisition_id",
        ),
    ),
)
def test_effective_locator_substitution_invalidates_binding(
    template_id: str, cartridge: str, details: dict[str, str], field: str
):
    item = _external_item(
        cartridge=cartridge,
        template_id=template_id,
        details=details,
    )
    changed = deepcopy(item)
    changed["details"][field] = "substituted"

    assert len(verified_explicit_action_bindings(item, clock=_clock())) == 1
    assert verified_explicit_action_bindings(changed, clock=_clock()) == ()


@pytest.mark.parametrize(
    ("section", "field", "value"),
    (
        ("item", "entity_id", "employee-b"),
        ("connection", "base_url", "https://other.example.invalid"),
        ("connection", "default_writeback_path", "/api/v2/other"),
        ("details", "writeback_path", "/api/v2/override"),
    ),
)
def test_target_or_payload_substitution_invalidates_binding(
    section: str, field: str, value: str
):
    item = _external_item(
        cartridge="sap_hcm",
        template_id="prepare_hcm_org_review",
        details={"pernr": "1001"},
    )
    changed = deepcopy(item)
    if section == "item":
        changed[field] = value
    elif section == "connection":
        changed["metadata"]["connection"][field] = value
    else:
        changed["details"][field] = value

    assert verified_explicit_action_bindings(changed, clock=_clock()) == ()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("action_kind", "substituted"),
        ("template_type", "substituted"),
        ("risk_level", "high"),
        ("mode_default", "preview"),
        ("requires_approval", False),
        ("writeback_contract", "substituted"),
        ("writeback_version", 2),
    ),
)
def test_template_contract_substitution_invalidates_binding(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object
):
    item = attach_explicit_action_binding(
        business_item(), template_id="request_owner_review", clock=_clock()
    )
    template = ACTION_TEMPLATES["request_owner_review"]
    monkeypatch.setitem(template, field, value)

    assert verified_explicit_action_bindings(item, clock=_clock()) == ()


def test_v1_binding_is_rejected_without_compatibility_fallback():
    item = attach_explicit_action_binding(
        business_item(), template_id="request_owner_review", clock=_clock()
    )
    _binding(item)["version"] = "control-room-action-binding/v1"

    assert verified_explicit_action_bindings(item, clock=_clock()) == ()


@pytest.mark.parametrize("missing", ("entity_kind", "entity_id"))
def test_binding_emission_rejects_missing_entity_identity(missing: str):
    item = business_item()
    item.pop(missing)

    with pytest.raises(ValueError, match=missing):
        attach_explicit_action_binding(
            item, template_id="request_owner_review", clock=_clock()
        )


def test_external_binding_rejects_missing_required_target():
    item = business_item(
        cartridge="sap_hcm",
        source_system="sap_hcm",
        details={"pernr": "1001"},
    )

    with pytest.raises(ValueError, match="base_url"):
        attach_explicit_action_binding(
            item, template_id="prepare_hcm_access_review", clock=_clock()
        )


def test_binding_list_is_bounded_before_verification_work():
    item = attach_explicit_action_binding(
        business_item(), template_id="request_owner_review", clock=_clock()
    )
    binding = _binding(item)
    item["metadata"]["explicit_action_bindings"] = [
        deepcopy(binding) for _ in range(MAX_EXPLICIT_ACTION_BINDINGS + 1)
    ]

    assert verified_explicit_action_bindings(item, clock=_clock()) == ()
