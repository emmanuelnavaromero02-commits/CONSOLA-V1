from __future__ import annotations

import unicodedata

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.routers import control_room
from app.services import control_room_service
from app.services.intelligence import history as intelligence_history


OPERATOR = {
    "id": 7,
    "role": "super_admin",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}
DATASET_READER = {
    "id": 8,
    "role": "viewer",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}
SECRET_SENTINELS = (
    "password-sentinel",
    "access-sentinel",
    "refresh-sentinel",
    "api-sentinel",
    "client-sentinel",
    "private-sentinel",
    "credentials-sentinel",
    "bearer-sentinel",
    "jwt-sentinel",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJqd3Qtc2VudGluZWwifQ.signature123",
    "uri-sentinel",
    "unicode-sentinel",
)
FORBIDDEN_KEYS = {
    "password",
    "access_token",
    "refresh_token",
    "api_key",
    "client_secret",
    "private_key",
    "credentials",
    "connection_string",
    "details",
    "metadata",
    "explicit_action_bindings",
    "authority_audit",
    "sql",
    "raw_sql",
    "dataset",
    "datasets",
    "connection",
    "payload",
    "receipt",
    "provenance",
    "tenant_id",
    "workspace_id",
    "run_id",
    "execution_id",
    "kb",
    "component_code",
    "live_candidates",
    "odata_entity",
    "fields_found",
    "fields_missing",
}


def client(user: dict | None) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    async def current_user():
        return user

    app.dependency_overrides[control_room.require_authenticated] = current_user
    app.include_router(control_room.router)
    return TestClient(app, raise_server_exceptions=True)


def internal_client() -> TestClient:
    app = FastAPI()

    async def internal_service():
        return "mcp-infra"

    app.dependency_overrides[control_room.verify_internal_api_key] = internal_service
    app.include_router(control_room.router)
    return TestClient(app, raise_server_exceptions=True)


def poison() -> dict:
    secret_fields = {
        "password": "password-sentinel",
        "access_token": "access-sentinel",
        "refresh_token": "refresh-sentinel",
        "api_key": "api-sentinel",
        "client_secret": "client-sentinel",
        "private_key": "-----BEGIN PRIVATE KEY-----private-sentinel",
        "credentials": "credentials-sentinel",
        "connection_string": "postgres://user:uri-sentinel@db/private",
    }
    return {
        **secret_fields,
        "description": "Bearer bearer-sentinel",
        "detail": "-----BEGIN PRIVATE KEY-----private-sentinel-----END PRIVATE KEY-----",
        "label": "password=password-sentinel; access_token=access-sentinel",
        "recommendation": "client_secret=client-sentinel",
        "fact": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJqd3Qtc2VudGluZWwifQ.signature123",
        "outcome_summary": "credentials=credentials-sentinel",
        "value": "postgres://user:uri-sentinel@db/private",
        "details": {
            **secret_fields,
            "value": "jwt-sentinel",
            "sql": "SELECT password-sentinel FROM x",
        },
        "metadata": {
            **secret_fields,
            "authority_audit": "private",
            "receipt": "private",
        },
        "unknown_list": [{**secret_fields, "payload": "refresh-sentinel"}],
        "pa\u200bssword": "unicode-sentinel",
        "ｐａｓｓｗｏｒｄ": "unicode-sentinel",
    }


def keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in keys(item)}
    return set()


def assert_safe(response) -> None:
    assert response.status_code == 200
    folded = unicodedata.normalize("NFKC", response.text).casefold()
    assert all(secret.casefold() not in folded for secret in SECRET_SENTINELS)
    normalized_keys = {
        unicodedata.normalize("NFKC", key) for key in keys(response.json())
    }
    assert not normalized_keys & FORBIDDEN_KEYS


def item() -> dict:
    return {
        "id": "business-1",
        "kind": "anomaly",
        "title": "Hecho confirmado",
        **poison(),
    }


# fmt: off
SURFACES = (
    ("/api/control-room/summary", control_room_service, "summary", {"total_anomalies": 0, "sources": [poison()]}),
    ("/api/control-room/dashboard", control_room_service, "dashboard", {"summary": {"total_items": 0, **poison()}, "domains": [{"label": "Talento", "datasets": [poison()]}], "items": [item()]}),
    ("/api/control-room/sap-successfactors/gold-kpis", control_room_service, "sap_successfactors_gold_kpis", {"widgets": [{"title": "Contratistas", "contractor_count": 0, "rows": [poison()], **poison()}]}),
    ("/api/control-room/sap-successfactors/talent-kpis", control_room_service, "sap_successfactors_talent_kpis", {"readiness": {"profiled_employees": 0, **poison()}, "widgets": [poison()]}),
    ("/api/control-room/sap-successfactors/talent/overview", control_room_service, "sap_successfactors_talent_overview", {"nine_box": {"cells": [poison()]}, **poison()}),
    ("/api/control-room/sap-successfactors/talent/9box", control_room_service, "sap_successfactors_talent_9box", {"status": "ready", "cells": [poison()]}),
    ("/api/control-room/sap-successfactors/talent/9box/core", control_room_service, "sap_successfactors_talent_9box_box", {"status": "ready", "roster": [poison()]}),
    ("/api/control-room/sap-successfactors/talent/anomalies", control_room_service, "sap_successfactors_talent_anomalies", {"status": "ready", "items": [poison()]}),
    ("/api/control-room/sap-successfactors/talent/metadata-readiness", control_room_service, "sap_successfactors_talent_metadata_readiness", {"status": "ready", "entities": [poison()], "live_preflight": poison()}),
    ("/api/control-room/ops/summary", control_room_service, "ops_summary", {"items": {"total": 0, **poison()}, **poison()}),
    ("/api/control-room/agents/ops", control_room_service, "agents_ops", {"agents": [poison()], "operational_diagnostics": [poison()]}),
    ("/api/control-room/alerts", control_room_service, "list_alerts", {"alerts": [item()], "summary": {"total": 0}}),
    ("/api/control-room/anomalies", control_room_service, "list_anomalies", {"anomalies": [item()], "sources": [poison()]}),
    ("/api/control-room/items/business-1", control_room_service, "get_item", item()),
    ("/api/control-room/items/business-1/impact", control_room_service, "get_item_impact", {"item_id": "business-1", "estimate": 0, "drivers": [poison()]}),
    ("/api/control-room/items/business-1/activity", control_room_service, "get_item_activity", {"item_id": "business-1", "activity": [poison()]}),
    ("/api/control-room/items/business-1/action-runs", control_room_service, "list_item_action_runs", {"item_id": "business-1", "action_runs": [{"status": "completed", **poison()}]}),
    ("/api/control-room/items/business-1/outcomes", control_room_service, "list_item_outcomes", {"item_id": "business-1", "outcomes": [poison()]}),
    ("/api/control-room/decision-intelligence/runs", intelligence_history, "list_runs", {"runs": [poison()]}),
    ("/api/control-room/decision-intelligence/runs/run-1", intelligence_history, "get_run", {"run": poison(), "snapshots": [poison()]}),
    ("/api/control-room/decision-intelligence/history", intelligence_history, "list_history", {"history": [poison()]}),
    ("/api/control-room/decision-intelligence/calibration", intelligence_history, "calibration_report", {"status": "ready", "calibration_buckets": [poison()], **poison()}),
)
# fmt: on
