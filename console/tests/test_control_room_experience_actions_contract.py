from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.schemas.control_room_experience_actions import ExperienceAction
from app.services.control_room.business_experience import build_business_experience
from app.services.control_room.business_experience_v2 import (
    build_business_experience_v2,
)
from control_room_surface_fixtures import (
    OPERATOR,
    VIEWER,
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
    fact = _fact(business_item())

    assert fact.item_id == "business-1"
    assert len(fact.actions) == 1
    action = fact.actions[0]
    assert action.model_dump() == {
        "item_id": "business-1",
        "template_id": TEMPLATE_ID,
        "label": "Solicitar revision de owner",
        "operation": "preview",
        "enabled": True,
        "requires_approval": True,
        "prerequisites": [
            {"code": "business_eligible", "satisfied": True},
            {"code": "evidence", "satisfied": True},
            {"code": "scope", "satisfied": True},
            {"code": "template", "satisfied": True},
            {"code": "permission", "satisfied": True},
            {"code": "non_terminal", "satisfied": True},
            {"code": "freshness", "satisfied": True},
            {"code": "source_binding", "satisfied": True},
        ],
        "disabled_reason": None,
        "method": "POST",
        "endpoint": "/api/control-room/items/business-1/action-preview",
    }


def test_missing_template_and_diagnostic_items_publish_no_actions():
    assert _fact(business_item(), enabled={"not_a_template"}).actions == []
    assert _response(source_state()).sections == []
    assert _response(business_item(data_status="missing")).sections == []


def test_permission_and_terminal_state_publish_no_action_metadata():
    assert _fact(business_item(), user=VIEWER).actions == []
    assert _fact(business_item(status="resolved")).actions == []
    assert _fact(business_item(execution_status="executed")).actions == []


def test_stale_action_is_safe_disabled_and_limited_to_one():
    fact = _fact(
        business_item(data_status="stale"),
        enabled={"request_owner_review", "create_followup_task"},
    )

    assert fact.stale is True
    assert len(fact.actions) == 1
    action = fact.actions[0]
    assert action.enabled is False
    assert action.disabled_reason == "Actualiza los datos antes de continuar."
    assert (
        next(
            value for value in action.prerequisites if value.code == "freshness"
        ).satisfied
        is False
    )


def test_incomplete_source_binding_disables_action_without_internal_detail():
    item = business_item()
    item.pop("entity_id")
    item.pop("evidence_refs")
    item["employee_id"] = "employee-1001"
    bind_runtime_row_evidence(
        item,
        locator_field="employee_id",
        observed_at=str(item["detected_at"]),
    )
    action = _fact(item).actions[0]

    assert action.enabled is False
    assert action.disabled_reason == (
        "Completa los datos requeridos antes de continuar."
    )
    serialized = action.model_dump_json()
    for forbidden in (
        "source_dataset",
        "metadata",
        "evidence_refs",
        "source_url",
        "payload_hash",
        "sql",
        "error",
    ):
        assert forbidden not in serialized


def test_action_schema_rejects_forged_bindings_and_unknown_states():
    valid = _fact(business_item()).actions[0].model_dump()
    invalid_payloads = []
    for key, value in (
        ("template_id", "fabricated_template"),
        ("label", "Internal SQL error"),
        ("requires_approval", False),
        ("operation", "execute"),
        ("method", "GET"),
        ("endpoint", "https://evil.example/action"),
    ):
        payload = deepcopy(valid)
        payload[key] = value
        invalid_payloads.append(payload)
    extra = deepcopy(valid)
    extra["metadata"] = {"secret": True}
    invalid_payloads.append(extra)
    unknown_prerequisite = deepcopy(valid)
    unknown_prerequisite["prerequisites"][0]["code"] = "unknown"
    invalid_payloads.append(unknown_prerequisite)

    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            ExperienceAction.model_validate(payload)


def test_action_schema_rejects_cross_item_endpoint_substitution():
    payload = _fact(business_item()).actions[0].model_dump()
    payload["endpoint"] = "/api/control-room/items/other/action-preview"

    with pytest.raises(ValidationError):
        ExperienceAction.model_validate(payload)
