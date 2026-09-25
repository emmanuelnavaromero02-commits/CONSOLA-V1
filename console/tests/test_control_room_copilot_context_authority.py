from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


TENANT_ID = "11111111-1111-1111-1111-111111111111"
WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"
DB_ID = "33333333-3333-3333-3333-333333333333"
FINGERPRINT = "live:0123456789abcdef"
SECRET = "api_key=raw-operational-secret"
VIEWER = {
    "id": 7,
    "email": "viewer@example.com",
    "role": "viewer",
    "active_tenant_id": TENANT_ID,
    "active_workspace_id": WORKSPACE_ID,
}
OPERATOR = {**VIEWER, "id": 8, "email": "operator@example.com", "role": "tenant_admin"}


@pytest.fixture
def modules():
    from app.routers import copilot, copilot_advanced

    return copilot, copilot_advanced


def _client(router, user: dict, *, bypass_csrf: bool = True) -> TestClient:
    from starlette.middleware.base import BaseHTTPMiddleware

    class InjectUser(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user = user
            return await call_next(request)

    app = FastAPI()
    app.add_middleware(InjectUser)
    app.include_router(router.router)
    app.dependency_overrides[router.require_authenticated] = lambda: user
    if bypass_csrf:
        app.dependency_overrides[router.require_csrf] = lambda: None
    return TestClient(app)


def _raw_recommendations() -> dict:
    return {
        "available": True,
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "recommendations": [
            {
                "id": DB_ID,
                "snapshot_id": DB_ID,
                "fingerprint": FINGERPRINT,
                "severity": "warning",
                "category": "control_room",
                "title": "Revisar señales",
                "body": "Hay una señal pendiente.",
                "status": "active",
                "action_href": "/control-room",
                "evidence": {"metadata": {"api_key": SECRET}},
            }
        ],
    }


def _raw_snapshot() -> dict:
    return {
        "available": True,
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "id": DB_ID,
        "status": "ready",
        "created_at": "2026-07-26T10:00:00+00:00",
        "errors": [{"error": SECRET}],
        "sources": [
            {
                "name": "control_room.ops_summary",
                "status": "ready",
                "data": {
                    "items": {"total": 4, "by_status": {"open": 2}},
                    "open_items_by_severity": {"high": 1},
                    "metadata": {"api_key": SECRET},
                },
            },
            {
                "name": "control_room.agents_ops",
                "status": "ready",
                "data": {
                    "summary": {"active_agents": 2},
                    "raw_agents": [{"owner_email": "owner@example.com"}],
                },
            },
            {
                "name": "control_room.sap_successfactors_talent_metadata_readiness",
                "status": "ready",
                "data": {
                    "status": "partial",
                    "summary": {"live_required_ready": 2, "api_key": SECRET},
                    "benchmark_internal": SECRET,
                },
            },
        ],
        "recommendations": _raw_recommendations()["recommendations"],
    }


def test_snapshot_and_refresh_require_operations_read(modules, monkeypatch):
    copilot, _ = modules
    latest = AsyncMock(return_value=_raw_snapshot())
    refresh = AsyncMock(return_value=_raw_snapshot())
    monkeypatch.setattr(copilot.copilot_context_service, "latest_snapshot", latest)
    monkeypatch.setattr(
        copilot.copilot_context_service, "collect_workspace_context", refresh
    )

    client = _client(copilot, VIEWER)
    assert client.get("/api/copilot/context/snapshot").status_code == 403
    assert client.post("/api/copilot/context/refresh", json={}).status_code == 403
    latest.assert_not_awaited()
    refresh.assert_not_awaited()


def test_operations_reader_without_write_cannot_refresh(modules, monkeypatch):
    from app.services import permissions

    copilot, _ = modules
    latest = AsyncMock(return_value=_raw_snapshot())
    refresh = AsyncMock(return_value=_raw_snapshot())
    monkeypatch.setitem(
        permissions.ROLE_PERMISSIONS,
        "viewer",
        {*permissions.ROLE_PERMISSIONS["viewer"], "operations.read"},
    )
    monkeypatch.setattr(copilot.copilot_context_service, "latest_snapshot", latest)
    monkeypatch.setattr(
        copilot.copilot_context_service, "collect_workspace_context", refresh
    )

    client = _client(copilot, VIEWER)
    assert client.get("/api/copilot/context/snapshot").status_code == 200
    assert client.post("/api/copilot/context/refresh", json={}).status_code == 403
    latest.assert_awaited_once()
    refresh.assert_not_awaited()


def test_viewer_recommendations_keep_safe_alias_without_raw_ids(modules, monkeypatch):
    copilot, _ = modules
    monkeypatch.setattr(
        copilot.copilot_context_service,
        "list_recommendations",
        AsyncMock(return_value=_raw_recommendations()),
    )
    dismiss = AsyncMock(
        return_value={
            "id": DB_ID,
            "fingerprint": FINGERPRINT,
            "status": "dismissed",
        }
    )
    monkeypatch.setattr(
        copilot.copilot_context_service,
        "dismiss_recommendation",
        dismiss,
    )
    audit = AsyncMock()
    monkeypatch.setattr(copilot.audit_service, "record_event", audit)

    client = _client(copilot, VIEWER)
    listed = client.get("/api/copilot/recommendations")
    dismissed = client.post(
        f"/api/copilot/recommendations/{FINGERPRINT}/dismiss", json={}
    )
    assert listed.status_code == 200
    assert dismissed.status_code == 403
    assert listed.json()["recommendations"][0]["id"] == FINGERPRINT
    combined = listed.text + dismissed.text
    for private in (DB_ID, TENANT_ID, WORKSPACE_ID, SECRET, "evidence", "snapshot_id"):
        assert private not in combined
    dismiss.assert_not_awaited()
    audit.assert_not_awaited()


def test_authorized_dismiss_passes_forensics_without_duplicate_audit(
    modules, monkeypatch
):
    copilot, _ = modules
    dismiss = AsyncMock(
        return_value={
            "id": DB_ID,
            "fingerprint": FINGERPRINT,
            "status": "dismissed",
        }
    )
    audit = AsyncMock()
    monkeypatch.setattr(
        copilot.copilot_context_service, "dismiss_recommendation", dismiss
    )
    monkeypatch.setattr(copilot.audit_service, "record_event", audit)

    response = _client(copilot, OPERATOR).post(
        f"/api/copilot/recommendations/{FINGERPRINT}/dismiss", json={}
    )

    assert response.status_code == 200
    dismiss.assert_awaited_once()
    assert dismiss.await_args.args == (OPERATOR, FINGERPRINT)
    assert dismiss.await_args.kwargs["ip"]
    assert dismiss.await_args.kwargs["user_agent"] == "testclient"
    audit.assert_not_awaited()


def test_shared_copilot_mutation_routes_declare_csrf_and_write(modules):
    from app.services.csrf import require_csrf

    copilot, _ = modules
    expected = {
        "/api/copilot/context/refresh": {"operations.read", "control_room.write"},
        "/api/copilot/recommendations/{recommendation_id}/dismiss": {
            "control_room.write"
        },
    }
    for path, permission_set in expected.items():
        route = next(route for route in copilot.router.routes if route.path == path)
        dependencies = [item.dependency for item in route.dependencies]
        assert require_csrf in dependencies
        declared = {
            dependency.required_permission
            for dependency in dependencies
            if hasattr(dependency, "required_permission")
        }
        assert permission_set <= declared


def test_shared_mutations_reject_missing_csrf_before_service(modules, monkeypatch):
    copilot, _ = modules
    refresh = AsyncMock(return_value=_raw_snapshot())
    dismiss = AsyncMock(return_value={"status": "dismissed"})
    monkeypatch.setattr(
        copilot.copilot_context_service, "collect_workspace_context", refresh
    )
    monkeypatch.setattr(
        copilot.copilot_context_service, "dismiss_recommendation", dismiss
    )
    client = _client(copilot, OPERATOR, bypass_csrf=False)

    assert client.post("/api/copilot/context/refresh", json={}).status_code == 403
    assert (
        client.post(
            f"/api/copilot/recommendations/{FINGERPRINT}/dismiss", json={}
        ).status_code
        == 403
    )
    refresh.assert_not_awaited()
    dismiss.assert_not_awaited()


def test_operator_snapshot_is_typed_and_redacted(modules, monkeypatch):
    copilot, _ = modules
    refresh = AsyncMock(return_value=_raw_snapshot())
    audit = AsyncMock()
    monkeypatch.setattr(
        copilot.copilot_context_service,
        "latest_snapshot",
        AsyncMock(return_value=_raw_snapshot()),
    )
    monkeypatch.setattr(
        copilot.copilot_context_service,
        "collect_workspace_context",
        refresh,
    )
    monkeypatch.setattr(copilot.audit_service, "record_event", audit)

    client = _client(copilot, OPERATOR)
    responses = [
        client.get("/api/copilot/context/snapshot"),
        client.post("/api/copilot/context/refresh", json={}),
    ]
    for response in responses:
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["sources"][0]["diagnostic"]["items"]["total"] == 4
        assert payload["sources"][1]["diagnostic"]["summary"]["active_agents"] == 2
        for private in (
            SECRET,
            DB_ID,
            TENANT_ID,
            WORKSPACE_ID,
            "raw_agents",
            "benchmark_internal",
        ):
            assert private not in response.text
    refresh.assert_awaited_once()
    assert refresh.await_args.args == (OPERATOR,)
    assert refresh.await_args.kwargs["generated_by"] == "manual"
    assert refresh.await_args.kwargs["persist"] is True
    assert refresh.await_args.kwargs["ip"]
    assert refresh.await_args.kwargs["user_agent"] == "testclient"
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_prompt_splits_dataset_and_operator_authority(modules, monkeypatch):
    _, advanced = modules
    services = advanced.control_room_service
    kpis = AsyncMock(
        return_value={"readiness": {"profiled_employees": 3}, "metadata": SECRET}
    )
    overview = AsyncMock(
        return_value={"readiness": {"calculable_employees": 2}, "metadata": SECRET}
    )
    ops = AsyncMock(return_value={"items": {"total": 4}, "metadata": SECRET})
    metadata = AsyncMock(
        return_value={
            "status": "partial",
            "summary": {"live_required_ready": 2},
            "raw": SECRET,
        }
    )
    agents = AsyncMock(
        return_value={"summary": {"active_agents": 2}, "metadata": SECRET}
    )
    monkeypatch.setattr(services, "sap_successfactors_talent_kpis", kpis)
    monkeypatch.setattr(services, "sap_successfactors_talent_overview", overview)
    monkeypatch.setattr(services, "ops_summary", ops)
    monkeypatch.setattr(
        services, "sap_successfactors_talent_metadata_readiness", metadata
    )
    monkeypatch.setattr(services, "agents_ops", agents)

    page = {"route": "/control-room"}
    viewer_text = await advanced._control_room_live_context_for_prompt(page, VIEWER)
    assert viewer_text is not None
    viewer_payload = json.loads(viewer_text)
    assert (
        viewer_payload["sap_successfactors_talent_kpis"]["readiness"][
            "profiled_employees"
        ]
        == 3
    )
    assert "ops_summary" not in viewer_payload
    assert "sap_successfactors_talent_metadata_readiness" not in viewer_payload
    assert "agents_ops" not in viewer_payload
    ops.assert_not_awaited()
    metadata.assert_not_awaited()
    agents.assert_not_awaited()

    operator_text = await advanced._control_room_live_context_for_prompt(page, OPERATOR)
    assert operator_text is not None
    operator_payload = json.loads(operator_text)
    assert operator_payload["ops_summary"]["items"]["total"] == 4
    assert operator_payload["agents_ops"]["summary"]["active_agents"] == 2
    assert SECRET not in operator_text


@pytest.mark.asyncio
async def test_persisted_prompt_omits_snapshot_for_viewer(modules, monkeypatch):
    copilot, _ = modules
    service = copilot.copilot_context_service
    latest = AsyncMock(return_value=_raw_snapshot())
    monkeypatch.setattr(service, "latest_snapshot", latest)
    monkeypatch.setattr(
        service, "list_recommendations", AsyncMock(return_value=_raw_recommendations())
    )

    viewer_text = await service.prompt_context_for_user(VIEWER)
    assert viewer_text is not None
    assert "snapshot" not in json.loads(viewer_text)
    latest.assert_not_awaited()
    assert FINGERPRINT in viewer_text
    for private in (SECRET, DB_ID, TENANT_ID, WORKSPACE_ID, "evidence"):
        assert private not in viewer_text

    operator_text = await service.prompt_context_for_user(OPERATOR)
    assert operator_text is not None
    assert "snapshot" in json.loads(operator_text)
    latest.assert_awaited_once()
    assert SECRET not in operator_text
