from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.schemas.control_room_experience_actions import ExperienceAction
from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.business_experience_v2 import (
    build_business_experience_v2,
)
from app.services.control_room.business_explicit_action_binding import (
    attach_explicit_action_binding,
)
from control_room_surface_fixtures import (
    OPERATOR,
    VIEWER,
    action_item,
    business_item,
    snapshot,
    source_state,
)
from control_room_runtime_evidence_fixture import bind_runtime_row_evidence


TEMPLATE_ID = "request_owner_review"
ENABLED_TEMPLATES = {TEMPLATE_ID}


def _response(item, *, user=OPERATOR, enabled=ENABLED_TEMPLATES):
    return build_business_experience_v2(
        snapshot(items=(item,)),
        user=user,
        enabled_template_ids=enabled,
    )


def _fact(item, *, user=OPERATOR, enabled=ENABLED_TEMPLATES):
    return _response(item, user=user, enabled=enabled).sections[0].facts[0]


def test_experience_v1_contract_remains_unchanged():
    payload = build_business_experience(snapshot(items=(business_item(),))).model_dump(
        mode="json", exclude_none=True
    )

    assert payload["schema_version"] == "control-room-experience/v1"
    fact = payload["sections"][0]["facts"][0]
    assert set(fact) == {
        "kind",
        "title",
        "severity",
        "observed_at",
        "stale",
        "entity_label",
        "metric",
    }
    assert "item_id" not in fact
    assert "actions" not in fact


def test_valid_action_has_exact_server_binding():
    fact = _fact(action_item())

    assert len(fact.actions) == 1
    action = fact.actions[0]
    assert action.model_dump() == {
        "action_handle": action.action_handle,
        "label": "Solicitar revision de owner",
        "enabled": True,
        "requires_approval": True,
        "disabled_reason": None,
    }
    assert len(action.action_handle) == 64


def test_missing_template_and_diagnostic_items_publish_no_actions():
    assert _fact(action_item(), enabled={"not_a_template"}).actions == []
    assert _response(source_state()).sections == []
    assert _response(business_item(data_status="missing")).sections == []


def test_permission_and_terminal_state_publish_no_action_metadata():
    assert _fact(action_item(), user=VIEWER).actions == []
    assert _fact(action_item(status="approved")).actions == []
    assert _fact(action_item(status="resolved")).actions == []
    assert _fact(action_item(execution_status="executed")).actions == []


def test_stale_action_is_safe_disabled_and_limited_to_one():
    fact = _fact(
        action_item(data_status="stale"),
        enabled={"request_owner_review", "create_followup_task"},
    )

    assert fact.stale is True
    assert len(fact.actions) == 1
    action = fact.actions[0]
    assert action.enabled is False
    assert action.disabled_reason == "Actualiza los datos antes de continuar."


def test_incomplete_source_binding_cannot_issue_an_action():
    item = business_item()
    item.pop("entity_id")
    item.pop("evidence_refs")
    item["employee_id"] = "employee-1001"
    bind_runtime_row_evidence(
        item,
        locator_field="employee_id",
        observed_at=str(item["detected_at"]),
    )
    with pytest.raises(ValueError, match="entity_id"):
        attach_explicit_action_binding(item, template_id="request_owner_review")


def test_action_schema_rejects_forged_bindings_and_unknown_states():
    valid = _fact(action_item()).actions[0].model_dump()
    invalid_payloads = []
    for key, value in (
        ("action_handle", "fabricated"),
        ("label", ""),
        ("operation", "execute"),
        ("method", "GET"),
        ("endpoint", "https://evil.example/action"),
        ("prerequisites", [{"code": "scope", "satisfied": True}]),
    ):
        payload = deepcopy(valid)
        payload[key] = value
        invalid_payloads.append(payload)
    extra = deepcopy(valid)
    extra["metadata"] = {"secret": True}
    invalid_payloads.append(extra)
    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            ExperienceAction.model_validate(payload)


def test_action_schema_rejects_public_endpoint_metadata():
    payload = _fact(action_item()).actions[0].model_dump()
    payload["endpoint"] = "/api/control-room/items/other/action-preview"

    with pytest.raises(ValidationError):
        ExperienceAction.model_validate(payload)


def test_operational_template_never_issues_for_business_experience():
    with pytest.raises(ValueError, match="source is incomplete"):
        attach_explicit_action_binding(
            business_item(cartridge="platform"),
            template_id="restore_data_source",
        )
