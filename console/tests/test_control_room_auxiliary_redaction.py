from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.routers import control_room
from app.schemas import control_room_legacy_responses as responses
from app.services import banxico_readiness, control_room_service, inegi_readiness
from app.services import sec_edgar_readiness
from app.services.intelligence import market_decision_validation
from control_room_auxiliary_http_harness import (
    AUXILIARY_FORBIDDEN_KEYS,
    SCOPED_OPERATOR,
    SCOPED_READER,
    lessons_payload,
    market_payload,
    readiness_payload,
    threshold_payload,
)
from control_room_public_http_harness import (
    SECRET_SENTINELS,
    assert_safe,
    client,
    internal_client,
    keys,
)


PUBLIC_CASES = (
    (
        "/api/control-room/banxico/readiness",
        banxico_readiness,
        "banxico_readiness",
        readiness_payload,
    ),
    (
        "/api/control-room/inegi/readiness",
        inegi_readiness,
        "inegi_readiness",
        readiness_payload,
    ),
    (
        "/api/control-room/sec-edgar/readiness",
        sec_edgar_readiness,
        "sec_edgar_readiness",
        readiness_payload,
    ),
    (
        "/api/control-room/sap-successfactors/market-validation",
        market_decision_validation,
        "get_validation",
        market_payload,
    ),
    (
        "/api/control-room/thresholds",
        control_room_service,
        "list_thresholds",
        threshold_payload,
    ),
    (
        "/api/control-room/lessons",
        control_room_service,
        "list_lessons",
        lessons_payload,
    ),
)


@pytest.mark.parametrize(
    ("path", "module", "service_name", "payload_factory"), PUBLIC_CASES
)
def test_auxiliary_gets_project_complete_http_json(
    path, module, service_name, payload_factory
):
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    with patch.object(module, service_name, AsyncMock(return_value=payload_factory())):
        response = client(SCOPED_OPERATOR).get(path)
    assert_safe(response)
    body = response.json()
    assert not keys(body) & AUXILIARY_FORBIDDEN_KEYS


def test_auxiliary_contracts_preserve_business_zeros_and_facts():
    readiness = responses.ControlRoomReadinessResponse.project(readiness_payload())
    assert readiness.usable_count == 0
    assert readiness.series[0].value == readiness.series[0].confidence == 0

    market = responses.ControlRoomMarketValidationResponse.project(market_payload())
    assert market.simulation.available is True
    assert market.orchestration.available is True
    assert market.source.employee_count == market.source.confidence == 0
    assert market.simulation.p10 == market.simulation.p50 == market.simulation.p90 == 0

    thresholds = responses.ControlRoomThresholdsResponse.project(threshold_payload())
    assert thresholds.summary.active == 0
    assert thresholds.thresholds[0].warning_value == 0
    assert thresholds.thresholds[0].enabled is False

    lessons = responses.ControlRoomLessonsResponse.project(lessons_payload())
    assert lessons.lessons[0].rule == "Mantener revision humana"
    assert lessons.lessons[0].confidence == 0


def _declared_permission(path: str) -> set[str]:
    route = next(route for route in control_room.router.routes if route.path == path)
    return {
        dependency.dependency.required_permission
        for dependency in route.dependencies
        if hasattr(dependency.dependency, "required_permission")
    }


@pytest.mark.parametrize(
    ("path", "model", "permission"),
    (
        (
            "/api/control-room/banxico/readiness",
            responses.ControlRoomReadinessResponse,
            "datasets.read",
        ),
        (
            "/api/control-room/inegi/readiness",
            responses.ControlRoomReadinessResponse,
            "datasets.read",
        ),
        (
            "/api/control-room/sec-edgar/readiness",
            responses.ControlRoomReadinessResponse,
            "datasets.read",
        ),
        (
            "/api/control-room/sap-successfactors/market-validation",
            responses.ControlRoomMarketValidationResponse,
            "operations.read",
        ),
        (
            "/api/control-room/thresholds",
            responses.ControlRoomThresholdsResponse,
            "operations.read",
        ),
        (
            "/api/control-room/lessons",
            responses.ControlRoomLessonsResponse,
            "datasets.read",
        ),
    ),
)
def test_auxiliary_gets_are_typed_and_classified(path, model, permission):
    route = next(route for route in control_room.router.routes if route.path == path)
    assert route.response_model is model
    assert _declared_permission(path) == {permission}


def test_market_validation_run_keeps_write_guard_and_projects_http_json():
    path = "/api/control-room/sap-successfactors/market-validation/run"
    route = next(route for route in control_room.router.routes if route.path == path)
    dependency_names = {
        dependency.dependency.__name__
        for dependency in route.dependencies
        if hasattr(dependency.dependency, "__name__")
    }
    assert route.response_model is responses.ControlRoomMarketValidationResponse
    assert _declared_permission(path) == {"control_room.write"}
    assert "require_csrf" in dependency_names
    with patch.object(
        market_decision_validation,
        "run_validation",
        AsyncMock(return_value=market_payload()),
    ):
        response = client(SCOPED_OPERATOR).post(
            path,
            headers={"Authorization": "Bearer unit-test"},
        )
    assert_safe(response)
    body = response.json()
    assert body["simulation"]["available"] is True
    assert body["orchestration"]["available"] is True
    assert not keys(body) & AUXILIARY_FORBIDDEN_KEYS


@pytest.mark.parametrize(
    ("path", "module", "service_name"),
    (
        (
            "/api/control-room/sap-successfactors/market-validation",
            market_decision_validation,
            "get_validation",
        ),
        ("/api/control-room/thresholds", control_room_service, "list_thresholds"),
    ),
)
def test_dataset_reader_cannot_reach_auxiliary_operational_services(
    path, module, service_name
):
    service = AsyncMock(side_effect=AssertionError("service reached"))
    with patch.object(module, service_name, service):
        response = client(SCOPED_READER).get(path)
    assert response.status_code == 403
    service.assert_not_awaited()


def _internal_context(permission: str, *cartridges: str) -> dict:
    return {
        "trusted": True,
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "permissions": [permission],
        "allowed_cartridges": list(cartridges),
    }


def _assert_internal_data_safe(response) -> None:
    assert response.status_code == 200
    data = response.json()["data"]
    assert not keys(data) & AUXILIARY_FORBIDDEN_KEYS
    folded = str(data).casefold()
    assert all(secret.casefold() not in folded for secret in SECRET_SENTINELS)


def test_internal_readiness_uses_same_typed_business_projection():
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    with (
        patch.object(
            control_room,
            "verify_signed_security_context",
            return_value=_internal_context("datasets.read", "banxico"),
        ),
        patch.object(
            banxico_readiness,
            "banxico_readiness",
            AsyncMock(return_value=readiness_payload()),
        ),
    ):
        response = internal_client().post(
            "/api/control-room/internal/read",
            json={"security_context": {"signed": "value"}, "view": "banxico_readiness"},
        )
    _assert_internal_data_safe(response)


def test_internal_market_validation_requires_operations_and_projects():
    service = AsyncMock(return_value=market_payload())
    with (
        patch.object(
            control_room,
            "verify_signed_security_context",
            return_value=_internal_context("datasets.read", "sap_successfactors"),
        ),
        patch.object(market_decision_validation, "get_validation", service),
    ):
        denied = internal_client().post(
            "/api/control-room/internal/read",
            json={
                "security_context": {"signed": "value"},
                "view": "sap_successfactors_market_validation",
            },
        )
    assert denied.status_code == 403
    service.assert_not_awaited()

    control_room._CONTROL_ROOM_READ_CACHE.clear()
    with (
        patch.object(
            control_room,
            "verify_signed_security_context",
            return_value=_internal_context("operations.read", "sap_successfactors"),
        ),
        patch.object(market_decision_validation, "get_validation", service),
    ):
        allowed = internal_client().post(
            "/api/control-room/internal/read",
            json={
                "security_context": {"signed": "value"},
                "view": "sap_successfactors_market_validation",
            },
        )
    _assert_internal_data_safe(allowed)
    assert "simulation_id" not in keys(allowed.json()["data"])
