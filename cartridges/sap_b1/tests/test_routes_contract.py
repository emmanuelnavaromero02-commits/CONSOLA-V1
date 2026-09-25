from __future__ import annotations

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
