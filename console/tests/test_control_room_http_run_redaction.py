from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.routers import control_room
from app.services import control_room_service


OPERATOR = {
    "id": 7,
    "role": "super_admin",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}
SENSITIVE_RUN_FIELDS = {
    "id": 91,
    "tenant_id": "tenant-a",
    "workspace_id": "workspace-a",
    "decision_id": 12,
    "legacy_execution_id": 13,
    "action_type": "private_action",
    "adapter_name": "PrivateAdapter",
    "idempotency_key": "private-key",
    "actor_id": 7,
    "actor_email": "private@example.invalid",
    "input": {"secret": "private"},
    "dry_run_result": {"payload": "private"},
    "execution_result": {"receipt": "private"},
    "side_effect": {"connection": "private"},
    "error_code": "private_code",
    "error_message": "private message",
    "metadata": {
        "authority_audit": {"binding_id": "private"},
        "reservation_lease_token": "private-token",
    },
    "authorityAudit": {"bindingId": "private"},
    "dryRunResult": {"payload": "private"},
    "errorCode": "private_code",
    "sideEffect": {"connectionId": "private"},
    "technicalMetadata": {"receiptDigest": "private"},
    "tenantId": "tenant-a",
}


def _client() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = OPERATOR
        return await call_next(request)

    async def current_user():
        return OPERATOR

    app.dependency_overrides[control_room.require_authenticated] = current_user
    app.include_router(control_room.router)
    return TestClient(app, raise_server_exceptions=True)


def _all_keys(value) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(key)
            keys.update(_all_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_all_keys(item))
    return keys


def test_service_action_run_projection_keeps_authority_audit_server_only():
    projected = control_room_service._action_run_public(
        {
            "id": 91,
            "metadata": {
                "authority_audit": {"binding_id": "server-only"},
                "reservation_lease_token": "server-only-token",
                "remote_attempt": {"status": "ambiguous"},
            },
        }
    )

    assert "authority_audit" not in projected["metadata"]
    assert "reservation_lease_token" not in projected["metadata"]
    assert projected["metadata"]["remote_attempt"]["status"] == "ambiguous"


@pytest.mark.parametrize(
    ("path", "service_name", "collection", "safe"),
    (
        (
            "/api/control-room/items/item-1/action-runs",
            "list_item_action_runs",
            "action_runs",
            {
                "mode": "external",
                "status": "pending_reconciliation",
                "risk_level": "high",
                "requires_approval": True,
                "approval_status": "approved",
                "created_at": "2026-07-26T10:00:00Z",
            },
        ),
        (
            "/api/control-room/items/item-1/outcomes",
            "list_item_outcomes",
            "outcomes",
            {
                "action_taken": "Revision humana",
                "predicted_value": 10,
                "actual_value": 9,
                "prediction_error": 1,
                "outcome_summary": "Revision completada",
                "learned_rule": "Mantener revision humana",
                "created_at": "2026-07-26T10:00:00Z",
            },
        ),
    ),
)
def test_action_history_http_models_allow_only_business_safe_fields(
    path: str,
    service_name: str,
    collection: str,
    safe: dict,
):
    raw_entry = {**SENSITIVE_RUN_FIELDS, **safe}
    service = AsyncMock(return_value={"item_id": "item-1", collection: [raw_entry]})
    with patch.object(control_room_service, service_name, service):
        response = _client().get(path)

    assert response.status_code == 200
    body = response.json()
    assert body["item_id"] == "item-1"
    assert not (_all_keys(body) & set(SENSITIVE_RUN_FIELDS))
    assert set(body[collection][0]).issubset(safe)
    assert body[collection][0].items() >= safe.items()
    assert "private" not in response.text.lower()
    assert "id" not in body[collection][0]


@pytest.mark.parametrize(
    "path",
    (
        "/api/control-room/items/item-1/action-preview",
        "/api/control-room/items/item-1/action-dry-run",
        "/api/control-room/items/item-1/execute",
        "/api/control-room/sap-successfactors/talent/actions/preview",
    ),
)
def test_gone_action_routes_still_require_authentication(path: str):
    app = FastAPI()
    app.include_router(control_room.router)

    response = TestClient(app, raise_server_exceptions=True).post(path, json={})

    assert response.status_code == 401
