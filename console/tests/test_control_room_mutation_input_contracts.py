from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.control_room_state_mutation_requests import (
    ControlRoomApprovalRequest,
    ControlRoomThresholdRequest,
)
from app.services import control_room_service
from control_room_public_http_harness import OPERATOR, client


AUTHORIZATION = {"Authorization": "Bearer test-control-room-token"}
MISSING = object()
APPROVE_ROUTES = (
    ("/api/control-room/anomalies/business-1/approve", "approve_anomaly"),
    ("/api/control-room/items/business-1/approve", "approve_item"),
)
INVALID_DECISION_IDS = (
    MISSING,
    "1",
    True,
    {"value": 1},
    [1],
    None,
    0,
    -1,
    1.0,
    9_223_372_036_854_775_808,
)


@pytest.mark.parametrize("value", INVALID_DECISION_IDS)
def test_approval_request_model_requires_a_strict_positive_integer(
    value: object,
) -> None:
    payload = {} if value is MISSING else {"decision_id": value}

    with pytest.raises(ValidationError):
        ControlRoomApprovalRequest.model_validate(payload)


@pytest.mark.parametrize(("path", "service_name"), APPROVE_ROUTES)
@pytest.mark.parametrize("value", INVALID_DECISION_IDS)
def test_approve_http_rejects_invalid_decision_before_service_or_db(
    path: str,
    service_name: str,
    value: object,
) -> None:
    payload = {} if value is MISSING else {"decision_id": value}
    service = AsyncMock()
    pool = AsyncMock(side_effect=AssertionError("database must not be reached"))
    with (
        patch.object(control_room_service, service_name, service),
        patch.object(control_room_service.auth, "pool", pool),
    ):
        response = client(OPERATOR).post(path, json=payload, headers=AUTHORIZATION)

    assert response.status_code == 422
    service.assert_not_awaited()
    pool.assert_not_awaited()


@pytest.mark.parametrize(("path", "service_name"), APPROVE_ROUTES)
def test_approve_http_rejects_empty_body_before_service_or_db(
    path: str,
    service_name: str,
) -> None:
    service = AsyncMock()
    pool = AsyncMock(side_effect=AssertionError("database must not be reached"))
    with (
        patch.object(control_room_service, service_name, service),
        patch.object(control_room_service.auth, "pool", pool),
    ):
        response = client(OPERATOR).post(
            path,
            content=b"",
            headers=AUTHORIZATION,
        )

    assert response.status_code == 422
    service.assert_not_awaited()
    pool.assert_not_awaited()


@pytest.mark.parametrize("value", ("false", "true", 0, 1, None, [], {}))
def test_threshold_request_model_rejects_non_boolean_enabled(value: object) -> None:
    with pytest.raises(ValidationError):
        ControlRoomThresholdRequest.model_validate({"enabled": value})


@pytest.mark.parametrize("method", ("POST", "PATCH"))
@pytest.mark.parametrize("value", ("false", "true", 0, 1, None, [], {}))
def test_threshold_http_rejects_non_boolean_before_service_or_db(
    method: str,
    value: object,
) -> None:
    service = AsyncMock()
    pool = AsyncMock(side_effect=AssertionError("database must not be reached"))
    with (
        patch.object(control_room_service, "upsert_threshold", service),
        patch.object(control_room_service.auth, "pool", pool),
    ):
        response = client(OPERATOR).request(
            method,
            "/api/control-room/thresholds",
            json={"enabled": value},
            headers=AUTHORIZATION,
        )

    assert response.status_code == 422
    service.assert_not_awaited()
    pool.assert_not_awaited()


@pytest.mark.parametrize("method", ("POST", "PATCH"))
def test_threshold_http_preserves_explicit_false(method: str) -> None:
    service = AsyncMock(return_value={"threshold": {"enabled": False}})
    with patch.object(control_room_service, "upsert_threshold", service):
        response = client(OPERATOR).request(
            method,
            "/api/control-room/thresholds",
            json={"enabled": False},
            headers=AUTHORIZATION,
        )

    assert response.status_code == 200
    assert response.json()["threshold"]["enabled"] is False
    assert service.await_args.args[0]["enabled"] is False


@pytest.mark.parametrize("method", ("POST", "PATCH"))
def test_threshold_http_preserves_empty_body_compatibility(method: str) -> None:
    service = AsyncMock(return_value={"threshold": {"enabled": True}})
    with patch.object(control_room_service, "upsert_threshold", service):
        response = client(OPERATOR).request(
            method,
            "/api/control-room/thresholds",
            content=b"",
            headers=AUTHORIZATION,
        )

    assert response.status_code == 200
    assert service.await_args.args[0] == {}


@pytest.mark.asyncio
async def test_threshold_service_rejects_string_false_before_database() -> None:
    pool = AsyncMock(side_effect=AssertionError("database must not be reached"))
    with (
        patch.object(control_room_service.auth, "pool", pool),
        pytest.raises(HTTPException) as exc_info,
    ):
        await control_room_service.upsert_threshold(
            {
                "cartridge_id": "replicon",
                "anomaly_type": "low_margin",
                "metric": "gross_margin",
                "enabled": "false",
            },
            {**OPERATOR, "allowed_cartridges": ["replicon"]},
        )

    assert exc_info.value.status_code == 422
    pool.assert_not_awaited()
