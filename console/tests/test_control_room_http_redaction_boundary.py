from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.routers import control_room
from app.schemas import control_room_legacy_responses as responses
from app.services import control_room_service
from control_room_public_http_harness import (
    DATASET_READER,
    FORBIDDEN_KEYS,
    OPERATOR,
    SECRET_SENTINELS,
    SURFACES,
    assert_safe,
    client,
    internal_client,
    keys,
    poison,
)


@pytest.mark.parametrize(("path", "module", "service_name", "payload"), SURFACES)
def test_all_public_surfaces_project_complete_http_json(
    path, module, service_name, payload
):
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    with patch.object(module, service_name, AsyncMock(return_value=payload)):
        assert_safe(client(OPERATOR).get(path))


def test_business_kpis_keep_real_zeros_and_authorized_facts():
    payload = {
        "tenant_id": "private",
        "workspace_id": "private",
        "widgets": [
            {
                "id": "sf_contractor_risk",
                "title": "Riesgo de contratistas",
                "value": 0,
                "contractor_count": 0,
                "risk_factor": 0,
                "rows": [
                    {"contractor_count": 0, "risk_factor": 0, "fact": "Sin incidencias"}
                ],
            }
        ],
    }
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    with patch.object(
        control_room_service,
        "sap_successfactors_gold_kpis",
        AsyncMock(return_value=payload),
    ):
        response = client(DATASET_READER).get(
            "/api/control-room/sap-successfactors/gold-kpis"
        )

    widget = response.json()["widgets"][0]
    assert widget["id"] == "sf_contractor_risk"
    assert widget["value"] == widget["contractor_count"] == widget["risk_factor"] == 0
    assert widget["rows"][0]["fact"] == "Sin incidencias"
    assert "tenant_id" not in response.json() and "workspace_id" not in response.json()


def test_talent_keeps_only_format_valid_business_identifiers():
    payload = {
        "status": "ready",
        "box": {"box_id": "core", "box_label": "Core"},
        "roster": [
            {"employee_key": "tal_abcdef123456", "display_name": "Colaborador 3456"},
            {"employee_key": "technical-id-sentinel", "display_name": "Descartado"},
        ],
    }
    with patch.object(
        control_room_service,
        "sap_successfactors_talent_9box_box",
        AsyncMock(return_value=payload),
    ):
        response = client(DATASET_READER).get(
            "/api/control-room/sap-successfactors/talent/9box/core"
        )

    body = response.json()
    assert body["box"]["box_id"] == "core"
    assert body["roster"][0]["employee_key"] == "tal_abcdef123456"
    assert "technical-id-sentinel" not in response.text


def test_talent_metadata_keeps_business_component_slug_only():
    payload = {
        "status": "ready",
        "entities": [
            {"id": "performance", "status": "available"},
            {"id": "connection-secret", "status": "ready"},
        ],
    }
    with patch.object(
        control_room_service,
        "sap_successfactors_talent_metadata_readiness",
        AsyncMock(return_value=payload),
    ):
        response = client(DATASET_READER).get(
            "/api/control-room/sap-successfactors/talent/metadata-readiness"
        )

    entities = response.json()["entities"]
    assert entities[0]["id"] == "performance"
    assert entities[1]["id"] is None


def test_optional_nested_projection_preserves_explicit_null():
    projected = responses.ControlRoomTalentKpisResponse.project(
        {"workforce_trends": None}
    )
    assert projected.workforce_trends is None


@pytest.mark.parametrize(
    "path",
    (
        "/api/control-room/dashboard",
        "/api/control-room/ops/summary",
        "/api/control-room/agents/ops",
        "/api/control-room/alerts",
        "/api/control-room/decision-intelligence/runs",
    ),
)
def test_dataset_reader_cannot_open_operational_surfaces(path):
    assert client(DATASET_READER).get(path).status_code == 403


def test_unknown_payload_fails_closed_without_echoing_content():
    raw = {"unknown": poison(), "other": [{"description": "password-sentinel"}]}
    with patch.object(control_room_service, "summary", AsyncMock(return_value=raw)):
        response = client(DATASET_READER).get("/api/control-room/summary")
    assert_safe(response)
    assert response.json()["total_anomalies"] == 0


def test_routes_are_typed_and_operational_families_require_operations_read():
    by_path = {
        route.path: route
        for route in control_room.router.routes
        if "GET" in (route.methods or set())
    }
    expected = {
        "/api/control-room/summary": (
            responses.ControlRoomBusinessSummaryResponse,
            "datasets.read",
        ),
        "/api/control-room/sap-successfactors/gold-kpis": (
            responses.ControlRoomGoldKpisResponse,
            "datasets.read",
        ),
        "/api/control-room/ops/summary": (
            responses.ControlRoomOpsSummaryResponse,
            "operations.read",
        ),
        "/api/control-room/agents/ops": (
            responses.ControlRoomAgentsOpsResponse,
            "operations.read",
        ),
        "/api/control-room/decision-intelligence/runs": (
            responses.ControlRoomDecisionIntelligenceRunsResponse,
            "operations.read",
        ),
        "/api/control-room/decision-intelligence/runs/{run_id}": (
            responses.ControlRoomDecisionIntelligenceRunDetailResponse,
            "operations.read",
        ),
        "/api/control-room/decision-intelligence/history": (
            responses.ControlRoomDecisionIntelligenceHistoryResponse,
            "operations.read",
        ),
        "/api/control-room/decision-intelligence/calibration": (
            responses.ControlRoomDecisionIntelligenceCalibrationResponse,
            "operations.read",
        ),
    }
    for path, (model, permission) in expected.items():
        route = by_path[path]
        assert route.response_model is model
        declared = {
            d.dependency.required_permission
            for d in route.dependencies
            if hasattr(d.dependency, "required_permission")
        }
        assert declared == {permission}


def _internal_context(permission: str) -> dict:
    return {
        "trusted": True,
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "permissions": [permission],
    }


def test_internal_dataset_reader_cannot_bypass_operational_boundary():
    service = AsyncMock(side_effect=AssertionError("service reached"))
    with (
        patch.object(
            control_room,
            "verify_signed_security_context",
            return_value=_internal_context("datasets.read"),
        ),
        patch.object(control_room_service, "ops_summary", service),
    ):
        response = internal_client().post(
            "/api/control-room/internal/read",
            json={"security_context": {"signed": "value"}, "view": "ops_summary"},
        )
    assert response.status_code == 403
    service.assert_not_awaited()


def test_internal_business_data_uses_same_fail_closed_projection():
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    with (
        patch.object(
            control_room,
            "verify_signed_security_context",
            return_value=_internal_context("datasets.read"),
        ),
        patch.object(
            control_room_service,
            "summary",
            AsyncMock(return_value={"total_anomalies": 0, **poison()}),
        ),
    ):
        response = internal_client().post(
            "/api/control-room/internal/read",
            json={"security_context": {"signed": "value"}, "view": "summary"},
        )
    data = response.json()["data"]
    assert data["total_anomalies"] == 0
    assert not keys(data) & FORBIDDEN_KEYS
    assert all(
        secret.casefold() not in str(data).casefold() for secret in SECRET_SENTINELS
    )


def test_internal_operations_reader_gets_only_typed_operational_data():
    with (
        patch.object(
            control_room,
            "verify_signed_security_context",
            return_value=_internal_context("operations.read"),
        ),
        patch.object(
            control_room_service,
            "ops_summary",
            AsyncMock(return_value={"items": {"total": 0}, **poison()}),
        ),
    ):
        response = internal_client().post(
            "/api/control-room/internal/read",
            json={"security_context": {"signed": "value"}, "view": "ops_summary"},
        )
    data = response.json()["data"]
    assert data["items"]["total"] == 0
    assert not keys(data) & FORBIDDEN_KEYS


@pytest.mark.parametrize(
    "path",
    (
        "/api/control-room/items/item-1/action-preview",
        "/api/control-room/items/item-1/action-dry-run",
        "/api/control-room/items/item-1/execute",
        "/api/control-room/sap-successfactors/talent/actions/preview",
    ),
)
def test_retired_action_posts_remain_authenticated_empty_410(path):
    response = client(OPERATOR).post(path, json=poison())
    assert response.status_code == 410 and response.content == b""
