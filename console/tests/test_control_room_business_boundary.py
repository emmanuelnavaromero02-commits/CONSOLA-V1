from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room.business_projection import (
    eligible_item_ids,
    filter_business_items,
    filter_by_eligible_parent,
)


USER = {
    "id": 7,
    "email": "ops@example.com",
    "role": "super_admin",
    "active_tenant_id": "tenant-A",
    "active_workspace_id": "workspace-A",
    "include_unready": True,
}


def _diagnostic_item() -> dict:
    return {
        "id": "source-state-1",
        "kind": "source_state",
        "cartridge": "sap_hcm",
        "domain": "Recursos Humanos",
        "module": "SAP HCM",
        "source_dataset": "workforce_cost_monthly",
        "title": "Source unavailable",
        "severity": "critical",
        "status": "open",
        "data_status": "missing",
        "details": {"source_status": "missing"},
        "omega": {"options": [{"id": "repair"}]},
        "priority_score": 99,
        "decision_id": 42,
    }


def _business_item() -> dict:
    return {
        "id": "business-1",
        "kind": "anomaly",
        "cartridge": "sap_hcm",
        "domain": "Recursos Humanos",
        "module": "SAP HCM",
        "source_dataset": "employees_anomalies",
        "entity_kind": "Empleado",
        "entity_id": "1001",
        "entity_label": "Ana Gomez",
        "anomaly_type": "terminated_but_active",
        "title": "Empleado terminado sigue activo",
        "description": "Validar acceso.",
        "recommendation": "Revisar baja.",
        "root_cause": "Estado inconsistente.",
        "impact": "Riesgo de acceso.",
        "severity": "critical",
        "severity_weight": 4,
        "status": "open",
        "detected_at": "2026-07-16T10:00:00Z",
        "details": {"salary_monthly_usd": 1000},
    }


def test_mixed_projection_excludes_diagnostics_from_business_relations():
    business = control_room_service._with_omega(_business_item())
    diagnostic = _diagnostic_item()
    items = filter_business_items([diagnostic, business])
    ids = eligible_item_ids([diagnostic, business])
    decisions = filter_by_eligible_parent(
        [
            {"id": 1, "item_id": "source-state-1"},
            {"id": 2, "item_id": "business-1"},
        ],
        ids,
    )

    assert [item["id"] for item in items] == ["business-1"]
    assert [row["id"] for row in decisions] == [2]
    assert control_room_service._alert_payload(items)["summary"]["total"] == 1
    assert control_room_service._dashboard_item_summary_counts(items) == {
        "total_items": 1,
        "total_anomalies": 1,
        "control_items": 0,
    }


def test_diagnostic_never_invokes_business_builders():
    impact = Mock(side_effect=AssertionError("impact builder invoked"))
    templates = Mock(side_effect=AssertionError("template builder invoked"))
    with (
        patch.object(control_room_service, "_impact_for_item", impact),
        patch.object(control_room_service, "_action_templates_for_item", templates),
    ):
        projected = control_room_service._with_omega(_diagnostic_item())

    impact.assert_not_called()
    templates.assert_not_called()
    assert "omega" not in projected
    assert "priority_score" not in projected
    assert "decision_id" not in projected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user",
    [USER, {**USER, "role": "super_admin", "include_unready": True}],
)
async def test_mutation_lookup_returns_409_for_visible_diagnostic(user):
    with patch.object(
        control_room_service,
        "_persisted_item_for_mutation",
        new=AsyncMock(return_value=_diagnostic_item()),
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service._item_for_mutation("source-state-1", user)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "item_not_business_eligible"
    assert exc.value.detail["reason"] == "source_state"


@pytest.mark.asyncio
async def test_mutation_lookup_returns_404_for_wrong_scope():
    with (
        patch.object(
            control_room_service,
            "_persisted_item_for_mutation",
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            control_room_service,
            "_collect_items",
            new=AsyncMock(return_value={"items": [], "diagnostics": []}),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service._item_for_mutation(
                "source-state-1",
                {**USER, "active_workspace_id": "workspace-B"},
            )

    assert exc.value.status_code == 404


COMMAND_CASES = (
    ("create_decision_for_item", ("source-state-1", USER), {}),
    ("select_item_option", ("source-state-1", "remediate", USER), {}),
    ("record_item_step", ("source-state-1", "signals", USER), {}),
    (
        "update_item_control",
        ("source-state-1", "control-1", {"status": "closed"}, USER),
        {},
    ),
    (
        "create_item_lesson",
        ("source-state-1", {"rule": "Validate owner first"}, USER),
        {},
    ),
    ("apply_item_lesson", ("source-state-1", 1, {}, USER), {}),
    ("action_preview", ("source-state-1", USER), {}),
    ("action_dry_run", ("source-state-1", USER), {}),
    ("run_auto_item", ("source-state-1", USER), {}),
    ("execute_item", ("source-state-1", USER), {"confirm_execute": True}),
    ("approve_item", ("source-state-1", USER), {"decision_id": 1}),
    ("dismiss_item", ("source-state-1", USER), {}),
    ("reopen_item", ("source-state-1", USER), {}),
    ("record_item_outcome", ("source-state-1", {"action_taken": "none"}, USER), {}),
    ("acknowledge_alert", ("source-state-1", USER), {}),
    ("snooze_alert", ("source-state-1", USER), {}),
    ("assign_alert", ("source-state-1", USER), {}),
    ("mark_alert_false_positive", ("source-state-1", USER), {}),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args,kwargs", COMMAND_CASES)
async def test_commands_stop_at_business_guard(name, args, kwargs):
    conflict = HTTPException(
        409,
        detail={"code": "item_not_business_eligible", "reason": "source_state"},
    )
    guard = AsyncMock(side_effect=conflict)
    pool = AsyncMock(side_effect=AssertionError("database reached"))
    audit = AsyncMock(side_effect=AssertionError("audit reached"))
    template = Mock(side_effect=AssertionError("template reached"))
    with (
        patch.object(control_room_service, "_item_for_mutation", guard),
        patch.object(control_room_service.auth, "pool", pool),
        patch.object(control_room_service.audit_service, "record_event", audit),
        patch.object(control_room_service, "_resolve_template", template),
    ):
        with pytest.raises(HTTPException) as exc:
            await getattr(control_room_service, name)(*args, **kwargs)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "item_not_business_eligible"
    guard.assert_awaited_once()
    pool.assert_not_awaited()
    audit.assert_not_awaited()
    template.assert_not_called()


def test_technical_state_survives_persistence_projection_round_trip():
    item = {
        **_business_item(),
        "id": "technical-anomaly",
        "data_status": "missing",
        "readiness_status": "insufficient_data",
    }
    impact = control_room_service._impact_for_item(item)
    metadata = control_room_service._metadata_for_item(item, impact)
    reloaded = control_room_service._persisted_intelligence_payload(
        {
            "item_id": item["id"],
            "item_kind": item["kind"],
            "cartridge_id": item["cartridge"],
            "domain": item["domain"],
            "source_dataset": item["source_dataset"],
            "title": item["title"],
            "severity": item["severity"],
            "status": item["status"],
            "metadata": metadata,
        }
    )

    projected = control_room_service._with_omega(reloaded)

    assert reloaded["data_status"] == "missing"
    assert reloaded["readiness_status"] == "insufficient_data"
    assert "omega" not in projected
    assert "action_templates" not in projected


def test_validated_parent_context_survives_builders_without_serializing():
    child = {
        **_business_item(),
        "id": "child-1",
        "kind": "intelligence_signal",
        "parent_item_id": "parent-1",
    }

    projected = control_room_service._with_omega(
        child,
        eligible_parent_ids={"parent-1"},
    )
    template = control_room_service._resolve_template(projected, None)
    metadata = control_room_service._metadata_for_item(
        projected,
        control_room_service._impact_for_item(projected),
    )

    assert projected["omega"]["options"]
    assert template["template_id"]
    assert metadata["parent_item_id"] == "parent-1"
    assert "eligible_parent" not in json.dumps(projected)


@pytest.mark.asyncio
async def test_lessons_exclude_ineligible_historical_parent():
    lessons = [
        {"id": 1, "item_id": "source-state-1", "rule": "technical"},
        {"id": 2, "item_id": "business-1", "rule": "business"},
    ]
    with (
        patch.object(
            control_room_service,
            "_load_lesson_rows",
            new=AsyncMock(return_value=lessons),
        ),
        patch.object(
            control_room_service,
            "_persisted_business_items",
            new=AsyncMock(return_value=[_business_item()]),
        ),
    ):
        result = await control_room_service.list_lessons(USER)

    assert [lesson["id"] for lesson in result["lessons"]] == [2]
    assert result["summary"]["total"] == 1
