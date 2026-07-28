from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.routers import control_room
from app.services import control_room_service
from control_room_mutation_contract_cases import (
    FAMILY_PAYLOADS,
    FORBIDDEN_KEYS,
    SECRET,
)
from control_room_public_http_harness import OPERATOR, client, keys


HTTP_CASES = (
    (
        "POST",
        "/api/control-room/alerts/business-1/ack",
        "acknowledge_alert",
        "alert",
        {},
    ),
    ("POST", "/api/control-room/alerts/business-1/snooze", "snooze_alert", "alert", {}),
    ("POST", "/api/control-room/alerts/business-1/assign", "assign_alert", "alert", {}),
    (
        "POST",
        "/api/control-room/alerts/business-1/false-positive",
        "mark_alert_false_positive",
        "alert",
        {},
    ),
    (
        "POST",
        "/api/control-room/items/business-1/step",
        "record_item_step",
        "step",
        {"step_id": "investigation"},
    ),
    (
        "POST",
        "/api/control-room/items/business-1/lessons",
        "create_item_lesson",
        "create_lesson",
        {},
    ),
    (
        "POST",
        "/api/control-room/items/business-1/outcomes",
        "record_item_outcome",
        "outcome",
        {},
    ),
    (
        "POST",
        "/api/control-room/items/business-1/lessons/7/apply",
        "apply_item_lesson",
        "apply_lesson",
        {},
    ),
    (
        "POST",
        "/api/control-room/items/business-1/control/review",
        "update_item_control",
        "control",
        {},
    ),
    (
        "POST",
        "/api/control-room/anomalies/business-1/decision",
        "create_decision_for_anomaly",
        "decision",
        {},
    ),
    (
        "POST",
        "/api/control-room/items/business-1/decision",
        "create_decision_for_item",
        "decision",
        {},
    ),
    (
        "POST",
        "/api/control-room/items/business-1/option",
        "select_item_option",
        "option",
        {"option_id": "review"},
    ),
    (
        "POST",
        "/api/control-room/anomalies/business-1/approve",
        "approve_anomaly",
        "approval",
        {"decision_id": 42},
    ),
    (
        "POST",
        "/api/control-room/items/business-1/approve",
        "approve_item",
        "approval",
        {"decision_id": 42},
    ),
    (
        "POST",
        "/api/control-room/items/business-1/dismiss",
        "dismiss_item",
        "dismiss",
        {},
    ),
    ("POST", "/api/control-room/items/business-1/reopen", "reopen_item", "reopen", {}),
    ("POST", "/api/control-room/thresholds", "upsert_threshold", "threshold", {}),
    ("PATCH", "/api/control-room/thresholds", "upsert_threshold", "threshold", {}),
)


@pytest.mark.parametrize(
    ("method", "path", "service_name", "family", "body"), HTTP_CASES
)
def test_all_eighteen_mutation_routes_project_deep_poison_before_http(
    method: str,
    path: str,
    service_name: str,
    family: str,
    body: dict[str, object],
) -> None:
    control_room._CONTROL_ROOM_READ_CACHE.clear()
    service = AsyncMock(return_value=FAMILY_PAYLOADS[family])
    with patch.object(control_room_service, service_name, service):
        response = client(OPERATOR).request(
            method,
            path,
            json=body,
            headers={"Authorization": "Bearer test-control-room-token"},
        )

    assert response.status_code == 200
    assert not keys(response.json()) & FORBIDDEN_KEYS
    assert SECRET not in json.dumps(response.json())
    service.assert_awaited_once()
