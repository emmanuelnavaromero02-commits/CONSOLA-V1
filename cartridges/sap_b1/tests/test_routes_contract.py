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
    assert len(entities) == 45
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

    monkeypatch.setattr("app.api.routes_console.preflight_for_extract", lambda: None)
    unscoped = request_context._sign_security_context({"trusted": True, "source": "console", "role": "admin"})
    resp = client.post("/business-parameters/refresh", headers=AUTH, json={"security_context": unscoped})
    assert resp.status_code == 403 and "scope is required" in resp.json()["detail"]
    resp = client.post("/extract-all", headers=AUTH, json={"security_context": unscoped})
    assert resp.status_code == 403
