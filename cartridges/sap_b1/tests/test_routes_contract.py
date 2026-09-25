from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

AUTH = {"X-Internal-Api-Key": "test-secret-key-not-default", "X-Internal-Service": "console"}
SAP_B1_VARS = (
    "SAP_B1_DIALECT", "SAP_B1_HOST", "SAP_B1_PORT", "SAP_B1_USER", "SAP_B1_PASSWORD",
    "SAP_B1_DATABASE", "SAP_B1_COMPANIES",
)


@pytest.fixture
def client(monkeypatch):
    for name in SAP_B1_VARS:
        monkeypatch.delenv(name, raising=False)
    from fastapi.testclient import TestClient

    from app import main

    main.catalog_service._seed_if_empty = lambda: None
    main.app.state.startup_ok = True
    main.app.state.startup_errors = []
    return TestClient(main.app)


def test_liveness_and_skill_discovery(client):
    assert client.get("/healthz").json() == {"ok": True, "service": "sap_b1"}
    assert client.get("/health").status_code == 200
    assert client.get("/skills/list").status_code == 401
    body = client.get("/skills/list", headers=AUTH).json()
    assert body["service"] == "sap_b1"
    names = {skill["name"] for skill in body["skills"]}
    assert {"/skills/test_connection", "/skills/run_incremental/{entity}", "/skills/get_watermarks"} <= names


def test_catalogue_comes_from_entities_yaml_when_postgres_is_absent(client):
    assert client.get("/entities").status_code == 401
    entities = client.get("/entities", headers=AUTH).json()["entities"]
    assert len(entities) == 48
    header = client.get("/entities/OINV/schema", headers=AUTH).json()
    assert header["primary_key"] == "DocEntry" and header["watermark_format"] == "b1_update_ts"
    assert header["watermark_ts_field"] == "UpdateTS" and header["parent"] is None
    line = client.get("/entities/INV1/schema", headers=AUTH).json()
    assert line["parent"] == "OINV" and line["parent_key"] == "DocEntry"
    assert client.get("/entities/NoExiste42/schema", headers=AUTH).status_code == 404


def test_extract_without_configuration_is_degraded_not_500(client):
    resp = client.post("/entities/OINV/extract", headers=AUTH)
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded" and body["configured"] is False
    assert {"SAP_B1_HOST", "SAP_B1_USER", "SAP_B1_PASSWORD", "SAP_B1_COMPANIES"} <= set(body["missing"])
    assert any(component["component"] == "sap_b1" for component in body["components"])
    assert client.post("/extract-all", headers=AUTH).status_code == 503
    assert client.post("/entities/OINV/extract").status_code == 401


def test_health_and_test_connection_report_degraded_without_secrets(client):
    resp = client.get("/health/sap_b1", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded" and body["configured"] is False and body["missing"]
    assert not {"host", "user", "password"} & set(body)
    skill = client.post("/skills/test_connection", headers=AUTH).json()
    assert skill["status"] == "degraded"


def test_mcp_tools_are_listed_behind_the_internal_key(client):
    assert client.get("/mcp/tools").status_code == 401
    tools = {tool["name"] for tool in client.get("/mcp/tools", headers=AUTH).json()["tools"]}
    assert {"list_entities", "get_schema", "preview", "extract", "extract_all", "query_kb"} <= tools


@pytest.mark.parametrize("path", ["/intercompany/refresh", "/business-parameters/refresh"])
def test_writes_to_bronze_need_a_signed_workspace_scope(client, path):
    assert client.post(path).status_code == 401
    assert client.post(path, headers=AUTH).status_code in {403, 503}
    forged = {"security_context": {"trusted": True, "tenant_id": "t", "workspace_id": "w"}}
    assert client.post(path, headers=AUTH, json=forged).status_code in {403, 503}


def test_a_signed_context_without_a_workspace_is_refused(client, monkeypatch):
    from app.core import request_context

    monkeypatch.setattr("app.api.routes_console.preflight_for_extract", lambda *_a: None)
    unscoped = request_context._sign_security_context({"trusted": True, "source": "console", "role": "admin"})
    resp = client.post("/business-parameters/refresh", headers=AUTH, json={"security_context": unscoped})
    assert resp.status_code == 403 and "scope is required" in resp.json()["detail"]
    resp = client.post("/extract-all", headers=AUTH, json={"security_context": unscoped})
    assert resp.status_code == 403


def test_finance_runs_need_a_signed_workspace_scope_and_a_valid_csv(client, monkeypatch):
    from app.core import request_context

    csv = "indicador,empresa,mes,dimension,clave,valor\nmargen_bruto,mx_mfg,2026-08,total,,1\n"
    assert client.post("/finance-runs", json={"csv": csv}).status_code == 401
    assert client.post("/finance-runs", headers=AUTH, json={"csv": csv}).status_code == 403
    forged = {"csv": csv, "security_context": {"trusted": True, "tenant_id": "t", "workspace_id": "w"}}
    assert client.post("/finance-runs", headers=AUTH, json=forged).status_code == 403
    scoped = request_context._sign_security_context(
        {"trusted": True, "source": "console", "role": "admin", "tenant_id": "t1", "workspace_id": "w1"}
    )
    stored = []
    monkeypatch.setattr("app.api.routes_console.load_finance_run", lambda text, ctx: stored.append(ctx) or {"entity": "FinanceManualRun", "rows": 1})
    monkeypatch.setattr("app.api.routes_console._mark_external_job", lambda *a: None)
    ok = client.post("/finance-runs", headers=AUTH, json={"csv": csv, "security_context": scoped})
    assert ok.status_code == 200 and stored and stored[0]["workspace_id"] == "w1"
    assert client.post("/finance-runs", headers=AUTH, json={"security_context": scoped}).status_code == 422


def test_parameter_catalog_validation_and_indicators(client):
    assert client.get("/business-parameters/catalog").status_code == 401
    keys = {item["key"] for item in client.get("/business-parameters/catalog", headers=AUTH).json()["parameters"]}
    assert {"margin_min_pct", "coverage_red_days", "expiry_red_days", "reconciliation_tolerance_pct"} <= keys
    good = client.post("/business-parameters/validate", headers=AUTH, json={"spec": "threshold:*:*:margin_min_pct=25"})
    assert good.status_code == 200 and good.json()["count"] == 1
    bad = client.post("/business-parameters/validate", headers=AUTH, json={"spec": "control:mx:2026-08:cogs=1"})
    assert bad.status_code == 422 and "unknown parameter kind" in bad.json()["detail"]
    items = client.get("/indicators", headers=AUTH).json()["indicators"]
    assert len(items) == 12 and {item["case"] for item in items} == {"finanzas", "ventas", "compras"}


TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"


def _signed(**fields) -> str:
    from app.core import request_context

    return json.dumps(request_context._sign_security_context({"trusted": True, "source": "console", "role": "admin", **fields}))


def test_connector_status_needs_the_internal_key_and_a_signed_workspace_scope(client, monkeypatch):
    reads: list[str] = []
    monkeypatch.setattr("app.api.routes_console._read_heartbeat", lambda name: reads.append(name))
    assert client.get("/connector/status").status_code == 401
    assert client.get("/connector/status", headers=AUTH).status_code == 403
    forged = json.dumps({"trusted": True, "tenant_id": TENANT, "workspace_id": WORKSPACE})
    for header in (forged, "not json", _signed(tenant_id=TENANT)):
        resp = client.get("/connector/status", headers={**AUTH, "x-security-context": header})
        assert resp.status_code == 403, header
    tampered = json.loads(_signed(tenant_id=TENANT, workspace_id=WORKSPACE))
    tampered["workspace_id"] = "33333333-3333-4333-8333-333333333333"
    resp = client.get("/connector/status", headers={**AUTH, "x-security-context": json.dumps(tampered)})
    assert resp.status_code == 403
    assert reads == [], "nothing is read from the bucket before the scope is verified"


def test_connector_status_returns_the_scope_heartbeat_with_its_age(client, monkeypatch):
    stored: dict[str, bytes] = {}
    monkeypatch.setattr("app.api.routes_console._read_heartbeat", lambda name: stored.get(name))
    headers = {**AUTH, "x-security-context": _signed(tenant_id=TENANT, workspace_id=WORKSPACE)}
    assert client.get("/connector/status", headers=headers).json() == {"present": False}

    at = (datetime.now(timezone.utc) - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    beat = {
        "schema_version": 1, "agent_version": "0.2.0", "at": at, "source_ok": True, "source_ms": 12,
        "source_error": None, "dialect": "mssql", "companies": ["mx_mfg"],
        "last_cycle": {"started_at": at, "finished_at": at, "status": "success", "entities_ok": 46, "entities_failed": 0},
        "initial_load": {"state": "done", "months_done": 25, "months_total": 25}, "next_cycle_at": at,
        "unexpected": "dropped",
    }
    key = f"raw/sap_b1/_agent/tenant_id={TENANT}/workspace_id={WORKSPACE}/heartbeat.json"
    stored[key] = json.dumps(beat).encode("utf-8")
    body = client.get("/connector/status", headers=headers).json()
    assert body["present"] is True and body["readable"] is True and 90 <= body["age_seconds"] < 150
    assert {k: body[k] for k in beat if k != "unexpected"} == {k: v for k, v in beat.items() if k != "unexpected"}
    assert "unexpected" not in body

    other = {**AUTH, "x-security-context": _signed(tenant_id=TENANT, workspace_id="33333333-3333-4333-8333-333333333333")}
    assert client.get("/connector/status", headers=other).json() == {"present": False}, "another workspace reads its own key"

    stored[key] = b"{not json"
    assert client.get("/connector/status", headers=headers).json() == {"present": True, "readable": False, "age_seconds": None}

    def broken(name):
        raise OSError("bucket unreachable")

    monkeypatch.setattr("app.api.routes_console._read_heartbeat", broken)
    resp = client.get("/connector/status", headers=headers)
    assert resp.status_code == 503 and resp.json()["status"] == "degraded"
