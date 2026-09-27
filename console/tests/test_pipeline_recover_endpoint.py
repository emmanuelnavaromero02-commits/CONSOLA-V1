from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "22222222-2222-2222-2222-222222222222"
DIGEST = "a" * 64
OPERATOR = {
    "id": 7,
    "email": "ops@example.com",
    "role": "workspace_admin",
    "active_tenant_id": TENANT,
    "active_workspace_id": WORKSPACE,
    "allowed_cartridges": ["sap_successfactors"],
}
CONSOLE = Path(__file__).resolve().parents[1]


def _router_module():
    return import_module("app.routers.pipeline_operations")


def _client(user):
    module = _router_module()
    application = FastAPI()

    @application.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    application.include_router(module.router)
    client = TestClient(application)
    client.cookies.set("csrf_token", "csrf-test-token")
    return client


HEADERS = {"X-CSRF-Token": "csrf-test-token"}


def _report(mode="dry_run", **overrides):
    service = import_module("app.domains.pipeline.stuck_run_recovery_service")
    counts = {
        "candidates": 1,
        "recoverable": 1,
        "recovered": 1 if mode == "applied" else 0,
        "synced_terminal": 0,
        "live": 0,
        "unverifiable": 0,
        "not_applicable": 0,
        "conflicts": 0,
        "airflow_neutralized": 1 if mode == "applied" else 0,
        "airflow_neutralize_failed": 0,
    }
    values = dict(
        mode=mode,
        checked_at=datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc),
        threshold_minutes=15,
        plan_digest=DIGEST,
        counts=counts,
        runs=[
            {
                "run_id": "manual__old",
                "dag_id": "sap_successfactors_extract",
                "cartridge": "sap_successfactors",
                "entity": "User",
                "status_before": "queued",
                "status_after": "failed" if mode == "applied" else None,
                "started_at": datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc),
                "age_minutes": 240,
                "created_today": True,
                "classification": "stalled_queued_paused_dag",
                "action": "mark_failed",
                "neutralize_airflow": True,
                "reason_es": "La corrida quedó en cola con el proceso pausado en Airflow.",
            }
        ],
        truncated=False,
        message_es="1 de 1 corridas revisadas se pueden cerrar o sincronizar con Airflow.",
    )
    values.update(overrides)
    return service.RecoveryReport(**values)


@pytest.fixture()
def recorded(monkeypatch):
    calls: list[dict] = []
    module = _router_module()

    async def fake_recover(user, **kwargs):
        calls.append({"user": user, **kwargs})
        return _report("applied" if kwargs["mode"] == "apply" else "dry_run")

    monkeypatch.setattr(module.recovery_service, "recover_stuck_runs", fake_recover)
    return calls


def test_recovery_is_a_dry_run_by_default(recorded):
    response = _client(OPERATOR).post("/api/pipelines/recover-stuck-runs", json={}, headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "pipeline-recovery/v1"
    assert body["mode"] == "dry_run"
    assert body["plan_digest"] == DIGEST
    assert set(body["counts"]) == {
        "candidates", "recoverable", "recovered", "synced_terminal", "live",
        "unverifiable", "not_applicable", "conflicts", "airflow_neutralized",
        "airflow_neutralize_failed",
    }
    assert body["runs"][0]["classification"] == "stalled_queued_paused_dag"
    call = recorded[0]
    assert call["mode"] == "dry_run"
    assert call["expected_plan_digest"] is None
    assert call["neutralize_airflow"] is True
    assert call["actor"] == "user:7"
    assert call["threshold"].total_seconds() >= 900


def test_apply_requires_and_forwards_the_reviewed_digest(recorded):
    client = _client(OPERATOR)
    missing = client.post("/api/pipelines/recover-stuck-runs", json={"apply": True}, headers=HEADERS)
    assert missing.status_code == 422
    stray = client.post("/api/pipelines/recover-stuck-runs", json={"plan_digest": DIGEST}, headers=HEADERS)
    assert stray.status_code == 422
    assert recorded == []
    applied = client.post(
        "/api/pipelines/recover-stuck-runs",
        json={"apply": True, "plan_digest": DIGEST, "cartridge": "sap_successfactors", "dag_id": "sap_successfactors_extract", "threshold_minutes": 30, "exclude_run_ids": ["manual__keep"]},
        headers=HEADERS,
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["mode"] == "applied"
    call = recorded[0]
    assert call["mode"] == "apply"
    assert call["expected_plan_digest"] == DIGEST
    assert call["cartridge"] == "sap_successfactors"
    assert call["dag_ids"] == ["sap_successfactors_extract"]
    assert call["exclude_run_ids"] == ["manual__keep"]
    assert call["threshold"].total_seconds() == 1800


def test_stale_digest_returns_409_with_the_new_plan(monkeypatch):
    module = _router_module()
    service = module.recovery_service

    async def changed(_user, **_kwargs):
        raise service.PlanChanged("b" * 64)

    monkeypatch.setattr(service, "recover_stuck_runs", changed)
    response = _client(OPERATOR).post(
        "/api/pipelines/recover-stuck-runs",
        json={"apply": True, "plan_digest": DIGEST},
        headers=HEADERS,
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["reason"] == "plan_changed"
    assert detail["plan_digest"] == "b" * 64
    assert detail["public_message"]


def test_recovery_requires_csrf(recorded):
    client = _client(OPERATOR)
    client.cookies.clear()
    response = client.post("/api/pipelines/recover-stuck-runs", json={})
    assert response.status_code == 403
    assert recorded == []


@pytest.mark.parametrize("role", ["viewer", "analyst", "workspace_user"])
def test_recovery_requires_pipelines_run(recorded, role):
    response = _client({**OPERATOR, "role": role}).post(
        "/api/pipelines/recover-stuck-runs", json={}, headers=HEADERS
    )
    assert response.status_code == 403
    assert recorded == []


def test_recovery_refuses_a_cartridge_outside_the_caller_scope(recorded):
    response = _client(OPERATOR).post(
        "/api/pipelines/recover-stuck-runs", json={"cartridge": "replicon"}, headers=HEADERS
    )
    assert response.status_code == 403
    assert recorded == []


def test_recovery_requires_an_active_workspace_scope():
    user = {"id": 7, "role": "workspace_admin", "allowed_cartridges": ["sap_successfactors"]}
    response = _client(user).post("/api/pipelines/recover-stuck-runs", json={}, headers=HEADERS)
    assert response.status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"threshold_minutes": 5},
        {"threshold_minutes": 2000},
        {"exclude_run_ids": ["bad id with spaces"]},
        {"exclude_run_ids": [f"r{i}" for i in range(201)]},
        {"cartridge": "../etc"},
        {"dag_id": "drop table"},
        {"plan_digest": "XYZ", "apply": True},
        {"neutralize_airflow": False},
        {"tenant_id": TENANT},
    ],
)
def test_request_model_is_strict(recorded, body):
    response = _client(OPERATOR).post("/api/pipelines/recover-stuck-runs", json=body, headers=HEADERS)
    assert response.status_code == 422, body
    assert recorded == []


def test_route_is_registered_on_every_console_surface():
    main_source = (CONSOLE / "app" / "main.py").read_text(encoding="utf-8")
    prefixes = main_source.split("_RBAC_DEPENDENCY_PREFIXES = (", 1)[1].split(")", 1)[0]
    assert '"/api/pipelines",' in prefixes
    assert "app.include_router(pipeline_operations_router.router)" in main_source
    registry = import_module("app.route_surface_registry")
    assert registry.classify_route_surface("/api/pipelines/recover-stuck-runs") == "frontend"
    rate_limits = import_module("app.services.request_rate_limits")
    assert rate_limits.RATE_LIMITS["pipeline_recover"][0] <= 10
    paths = {route.path for route in _router_module().router.routes}
    assert "/api/pipelines/recover-stuck-runs" in paths


def test_recovery_without_a_cartridge_only_reads_visible_cartridges(recorded):
    response = _client(OPERATOR).post("/api/pipelines/recover-stuck-runs", json={}, headers=HEADERS)
    assert response.status_code == 200
    call = recorded[0]
    assert call["cartridge"] is None
    assert call["visible_cartridges"] == ["sap_successfactors"]
    assert call["orphan_scan"] is True


def test_restricted_user_without_cartridges_sees_no_runs(recorded):
    user = {**OPERATOR, "allowed_cartridges": []}
    response = _client(user).post("/api/pipelines/recover-stuck-runs", json={}, headers=HEADERS)
    assert response.status_code == 200
    assert recorded[0]["visible_cartridges"] == []


def test_wildcard_scope_is_not_filtered(recorded):
    user = {**OPERATOR, "allowed_cartridges": ["*"]}
    response = _client(user).post("/api/pipelines/recover-stuck-runs", json={}, headers=HEADERS)
    assert response.status_code == 200
    assert recorded[0]["visible_cartridges"] is None


def test_orphan_rows_serialize_in_the_strict_response(monkeypatch):
    module = _router_module()
    report = _report()
    report.runs[0].update(
        classification="airflow_orphan", action="neutralize_airflow", status_before="failed"
    )

    async def orphan(_user, **_kwargs):
        return report

    monkeypatch.setattr(module.recovery_service, "recover_stuck_runs", orphan)
    response = _client(OPERATOR).post("/api/pipelines/recover-stuck-runs", json={}, headers=HEADERS)
    assert response.status_code == 200
    run = response.json()["runs"][0]
    assert (run["classification"], run["action"]) == ("airflow_orphan", "neutralize_airflow")
