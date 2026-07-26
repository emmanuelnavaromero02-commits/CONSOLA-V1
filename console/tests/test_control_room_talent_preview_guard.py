from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.services import control_room_service
from app.services.control_room.business_explicit_action_binding import (
    attach_explicit_action_binding,
)
from control_room_runtime_evidence_fixture import bind_runtime_row_evidence
from test_control_room_permissions import _build_real_control_room_router_client


USER = {
    "id": 7,
    "email": "ops@example.com",
    "role": "user",
    "workspace_role": "workspace_admin",
    "active_tenant_id": "tenant-A",
    "active_workspace_id": "workspace-A",
    "allowed_cartridges": ["sap_successfactors"],
}


def _talent_item() -> dict:
    item = {
        "id": "talent-action-1",
        "kind": "anomaly",
        "cartridge": "sap_successfactors",
        "source_dataset": "sap_successfactors_talent_action_candidates",
        "source_system": "sap_successfactors",
        "tenant_id": "tenant-A",
        "workspace_id": "workspace-A",
        "title": "Revisar calibracion",
        "recommendation": "Validar cohorte con RRHH.",
        "severity": "high",
        "affected_count": 4,
        "observed_value": 4,
        "population_count": 10,
        "entity_id": "talent-cohort-1",
        "method": "cut_sensitivity",
        "detected_at": "2026-07-16T10:00:00Z",
        "metric_type": "count",
        "evaluation_status": "success",
        "data_status": "ready",
        "status": "open",
        "execution_status": "not_started",
        "entity_kind": "employee",
        "metadata": {
            "connection": {
                "base_url": "https://successfactors.example.invalid",
                "default_writeback_path": "/odata/v2/User",
            }
        },
    }
    return bind_runtime_row_evidence(
        item,
        locator_field="entity_id",
        observed_at="2026-07-16T10:00:00Z",
    )


@pytest.mark.asyncio
async def test_talent_preview_missing_action_is_404_without_fallback():
    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            AsyncMock(side_effect=HTTPException(404, "control room item not found")),
        ),
        patch.object(
            control_room_service, "query_dataset_rows", AsyncMock(return_value=[])
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.sap_successfactors_talent_action_preview(
                USER, {"action_id": "missing"}
            )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_talent_preview_propagates_diagnostic_conflict():
    conflict = HTTPException(
        409, detail={"code": "item_not_business_eligible", "reason": "source_state"}
    )
    with (
        patch.object(
            control_room_service, "_item_for_mutation", AsyncMock(side_effect=conflict)
        ),
        patch.object(
            control_room_service, "query_dataset_rows", AsyncMock(return_value=[])
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.sap_successfactors_talent_action_preview(
                USER, {"action_id": "technical"}
            )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_talent_preview_uses_real_item_and_server_template():
    template_id = "prepare_successfactors_review"
    item = attach_explicit_action_binding(_talent_item(), template_id=template_id)
    binding = item["metadata"]["explicit_action_bindings"][0]
    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            AsyncMock(return_value=item),
        ) as lookup,
        patch.object(
            control_room_service, "query_dataset_rows", AsyncMock(return_value=[])
        ),
        patch.object(
            control_room_service,
            "require_enabled_action_template_for_user",
            AsyncMock(),
        ),
    ):
        result = await control_room_service.sap_successfactors_talent_action_preview(
            USER,
            {
                "action_id": "talent-action-1",
                "template_id": template_id,
                "binding_id": binding["binding_id"],
            },
        )

    lookup.assert_awaited_once_with("talent-action-1", USER)
    assert result["action_id"] == "talent-action-1"
    assert result["method"] == "cut_sensitivity"
    assert result["template_id"] == template_id
    assert result["template"]["action_kind"] == "successfactors_employee_review"


def test_talent_preview_route_is_gone_without_reaching_service():
    analyst = {
        "id": 8,
        "email": "analyst@example.com",
        "role": "user",
        "workspace_role": "analyst",
    }
    service = AsyncMock()
    with patch.object(
        control_room_service, "sap_successfactors_talent_action_preview", service
    ):
        client: TestClient = _build_real_control_room_router_client(analyst)
        response = client.post(
            "/api/control-room/sap-successfactors/talent/actions/preview",
            headers={"authorization": "Bearer test"},
            json={"action_id": "talent-action-1"},
        )
    assert response.status_code == 410
    assert response.content == b""
    service.assert_not_awaited()


@pytest.mark.asyncio
async def test_talent_metadata_and_preview_remain_recommendation_only(monkeypatch):
    async def fake_rows(dataset: str, _user: dict | None, _limit: int) -> list[dict]:
        if dataset == "sap_successfactors_talent_cpa_scores":
            return [
                {"user_id": "100", "cpa_status": "ready"},
                {"user_id": "101", "cpa_status": "insufficient_data"},
            ]
        return []

    monkeypatch.setattr(control_room_service, "query_dataset_rows", fake_rows)
    readiness = await control_room_service.sap_successfactors_talent_metadata_readiness(
        USER
    )
    item = attach_explicit_action_binding(
        _talent_item(), template_id="prepare_successfactors_review"
    )
    binding = item["metadata"]["explicit_action_bindings"][0]
    with monkeypatch.context() as context:
        context.setattr(
            control_room_service,
            "_item_for_mutation",
            AsyncMock(return_value=item),
        )
        context.setattr(
            control_room_service,
            "require_enabled_action_template_for_user",
            AsyncMock(),
        )
        preview = await control_room_service.sap_successfactors_talent_action_preview(
            USER,
            {
                "action_id": item["id"],
                "box_id": "core",
                "template_id": binding["template_id"],
                "binding_id": binding["binding_id"],
            },
        )

    assert readiness["status"] == "partial"
    assert readiness["summary"]["cpa_ready_employees"] == 1
    assert preview["status"] == "preview_only"
    assert preview["write_back_enabled"] is False
    assert preview["compensation_enabled"] is False
    assert preview["recommendation_only"] is True
    assert preview["external_mutations"] == []
