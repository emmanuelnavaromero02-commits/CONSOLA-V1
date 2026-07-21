from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.services import control_room_service
from test_control_room_permissions import _build_real_control_room_router_client


USER = {
    "id": 7,
    "email": "ops@example.com",
    "role": "user",
    "workspace_role": "workspace_admin",
    "active_tenant_id": "tenant-A",
    "active_workspace_id": "workspace-A",
}


def _talent_item() -> dict:
    return {
        "id": "talent-action-1",
        "kind": "anomaly",
        "cartridge": "sap_successfactors",
        "source_dataset": "sap_successfactors_talent_action_candidates",
        "title": "Revisar calibracion",
        "recommendation": "Validar cohorte con RRHH.",
        "severity": "high",
        "affected_count": 4,
        "method": "cut_sensitivity",
        "detected_at": "2026-07-16T10:00:00Z",
        "metric_type": "count",
        "evaluation_status": "success",
        "evidence": {"source_dataset": "sap_successfactors_talent_action_candidates"},
    }


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
    template = {
        "template_id": "prepare_successfactors_talent_review",
        "label": "Preparar revision de talento",
        "action_kind": "successfactors_talent_review",
    }
    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            AsyncMock(return_value=_talent_item()),
        ) as lookup,
        patch.object(
            control_room_service, "query_dataset_rows", AsyncMock(return_value=[])
        ),
        patch.object(control_room_service, "_resolve_template", return_value=template),
    ):
        result = await control_room_service.sap_successfactors_talent_action_preview(
            USER, {"action_id": "talent-action-1"}
        )

    lookup.assert_awaited_once_with("talent-action-1", USER)
    assert result["action_id"] == "talent-action-1"
    assert result["method"] == "cut_sensitivity"
    assert result["template_id"] == template["template_id"]
    assert result["template"]["action_kind"] == "successfactors_talent_review"


def test_talent_preview_route_requires_control_room_write():
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
    assert response.status_code == 403
    service.assert_not_awaited()
