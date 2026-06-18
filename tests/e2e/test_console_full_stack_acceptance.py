"""Full-stack acceptance checks for the running local platform.

These tests are deliberately heavier than Playwright/browser smoke:
they exercise authenticated console APIs, Studio tools, Copilot durable
surfaces, a real HubSpot cartridge extraction against a deterministic fake
upstream, Bronze files in MinIO, Silver refresh, Gold materialization, catalog,
semantic, Control Room, settings and user surfaces.

Run through scripts/run_full_stack_acceptance.sh. The module skips unless
OMEGA_FULL_STACK_ACCEPTANCE=1 is set so normal unit runs stay fast.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest


if os.environ.get("OMEGA_FULL_STACK_ACCEPTANCE") != "1":
    pytest.skip("full-stack acceptance is opt-in", allow_module_level=True)


REPO = Path(__file__).resolve().parents[2]
HUBSPOT = os.environ.get("OMEGA_HUBSPOT_BASE", "http://127.0.0.1:8210")
REQUIRED_CARTRIDGES = {"replicon", "hubspot", "sap_hcm", "sap_s4hana", "sap_successfactors"}


def _csrf(client: httpx.Client) -> dict[str, str]:
    token = ""
    for cookie in client.cookies.jar:
        if cookie.name in {"csrf_token", "csrftoken"}:
            token = cookie.value
            if not cookie.domain:
                break
    return {"X-CSRF-Token": token} if token else {}


def _env_value(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    env_path = REPO / "infra/.env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _hubspot_headers() -> dict[str, str]:
    key = _env_value("INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE")
    assert key, "INTERNAL_API_KEY_CONSOLE_TO_CARTRIDGE missing"
    return {
        "X-Internal-Api-Key": key,
        "X-Internal-Service": "console",
    }


def _hubspot_invoke(tool: str, args: dict[str, Any] | None = None, *, timeout: float = 30.0) -> dict:
    with httpx.Client(base_url=HUBSPOT, headers=_hubspot_headers(), timeout=timeout) as client:
        response = client.post("/mcp/invoke", json={"tool": tool, "args": args or {}})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "error" not in payload, payload
    return payload.get("result", payload)


def _console_hubspot_invoke(
    client: httpx.Client,
    tool: str,
    args: dict[str, Any] | None = None,
    *,
    timeout: float = 30.0,
) -> dict:
    response = client.post(
        "/api/mcp/servers/hubspot/invoke",
        json={"tool": tool, "args": args or {}},
        headers=_csrf(client),
        timeout=timeout,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for_hubspot_job(
    client: httpx.Client,
    job_id: str,
    *,
    timeout_seconds: int = 90,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _console_hubspot_invoke(client, "get_job_status", {"job_id": job_id})
        status = str(last.get("status") or "")
        if status in {"done", "failed"}:
            assert status == "done", last
            return last
        time.sleep(1)
    pytest.fail(f"HubSpot job {job_id} did not finish; last={last}")


def _psql_scalar(sql: str) -> str:
    cmd = [
        "docker", "exec", "mode_postgres", "psql",
        "-U", "postgres", "-d", "modecissions", "-Atc", sql,
    ]
    completed = subprocess.run(cmd, cwd=REPO, check=True, text=True, capture_output=True)
    return completed.stdout.strip()


def _host_file_for_s3_uri(uri: str) -> Path:
    assert uri.startswith("s3://lakehouse/"), uri
    key = uri.removeprefix("s3://lakehouse/")
    return REPO / "data/lakehouse/lakehouse" / key


def test_01_console_surfaces_are_connected(admin_session: httpx.Client):
    me = admin_session.get("/api/me")
    assert me.status_code == 200, me.text
    assert me.json().get("email") or me.json().get("user", {}).get("email")

    access = admin_session.get("/api/me/access")
    assert access.status_code == 200, access.text
    access_body = access.json()
    assert access_body.get("ui_capabilities", {}).get("can_view_cartridges") is True

    cartridges = admin_session.get("/api/cartridges")
    assert cartridges.status_code == 200, cartridges.text
    assert REQUIRED_CARTRIDGES.issubset(set(cartridges.json().get("cartridges") or []))

    users = admin_session.get("/api/admin/users")
    assert users.status_code == 200, users.text
    assert "password_hash" not in users.text.lower()

    settings = admin_session.get("/api/settings")
    assert settings.status_code == 200, settings.text
    assert "settings" in settings.json()

    bad_password = admin_session.post(
        "/api/me/change-password",
        json={"current_password": "wrong-current-password", "new_password": "NotActuallyChanging123!"},
        headers=_csrf(admin_session),
    )
    assert bad_password.status_code in {400, 401, 403}, bad_password.text

    for path in ("/api/control-room/summary", "/api/control-room/dashboard", "/api/dashboard/kpis"):
        response = admin_session.get(path)
        assert response.status_code == 200, f"{path}: {response.text}"
        assert isinstance(response.json(), dict)


def test_02_studio_copilot_semantic_and_knowledge_surfaces(admin_session: httpx.Client):
    studio_tools = admin_session.get("/studio_ops/mcp/tools")
    assert studio_tools.status_code == 200, studio_tools.text
    tool_names = {tool.get("name") for tool in studio_tools.json().get("tools", [])}
    assert {"list_entities", "update_entity", "get_entity_logs"}.issubset(tool_names)

    entities = admin_session.post(
        "/studio_ops/mcp/invoke",
        json={"tool": "list_entities", "args": {"cartridge_id": "hubspot"}},
        headers=_csrf(admin_session),
    )
    assert entities.status_code == 200, entities.text
    assert entities.json().get("count", 0) >= 6

    openapi = {
        "openapi": "3.0.0",
        "components": {
            "schemas": {
                "AcceptanceEntity": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "string"},
                        "amount": {"type": "number"},
                        "closed_at": {"type": "string", "format": "date-time"},
                    },
                }
            }
        },
    }
    introspection = admin_session.post(
        "/api/studio/introspect-source",
        json={
            "cartridge_id": "hubspot",
            "source_kind": "openapi",
            "spec_content": json.dumps(openapi),
        },
        headers=_csrf(admin_session),
    )
    assert introspection.status_code == 200, introspection.text
    body = introspection.json()
    assert body["source"] == "live", body
    fields = body["entities"][0]["fields"]
    assert {field["name"]: field["type"] for field in fields} == {
        "amount": "float",
        "closed_at": "timestamp",
        "id": "string",
    }

    semantic = admin_session.get("/api/semantic?cartridge=hubspot")
    assert semantic.status_code == 200, semantic.text
    assert len(semantic.json().get("entities") or []) >= 6

    fact = f"acceptance-fact-{uuid.uuid4().hex[:8]}"
    add_fact = admin_session.post(
        "/api/copilot/memory/fact",
        json={"fact": fact, "source": "explicit"},
        headers=_csrf(admin_session),
    )
    assert add_fact.status_code == 200, add_fact.text
    memory = admin_session.get("/api/copilot/memory")
    assert memory.status_code == 200, memory.text
    assert fact in memory.text

    draft = admin_session.post(
        "/api/copilot/drafts",
        json={"kind": "memo", "title": "Acceptance", "body": "Pipeline status validated.", "tone": "neutral"},
        headers=_csrf(admin_session),
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["draft"]["status"] == "draft"

    workflow = admin_session.post(
        "/api/copilot/workflow",
        json={"intent": "Verify HubSpot acceptance pipeline"},
        headers=_csrf(admin_session),
    )
    assert workflow.status_code == 200, workflow.text
    workflow_id = workflow.json()["workflow"]["id"]
    status = admin_session.get(f"/api/copilot/workflow/{workflow_id}/status")
    assert status.status_code == 200, status.text
    assert status.json()["status"] in {"planning", "running", "completed", "failed", "cancelled"}

    kbs = _hubspot_invoke("list_kbs")
    assert {kb["kb_id"] for kb in kbs} >= {"open_pipeline_by_owner", "deals_closing_this_month"}


def test_03_hubspot_extracts_to_bronze_and_refreshes_silver(admin_session: httpx.Client):
    connection = admin_session.post(
        "/api/cartridges/hubspot/test_connection",
        json={},
        headers=_csrf(admin_session),
    )
    assert connection.status_code == 200, connection.text
    assert connection.json().get("ok") is True, connection.json()

    extracted: dict[str, dict] = {}
    for entity in ("owners", "pipelines", "deals"):
        started_response = admin_session.post(
            "/api/mcp/servers/hubspot/invoke",
            json={"tool": "extract", "args": {"entity": entity, "mode": "full"}},
            headers=_csrf(admin_session),
            timeout=30,
        )
        assert started_response.status_code == 200, started_response.text
        started = started_response.json()
        assert started.get("job_id"), started
        finished = _wait_for_hubspot_job(admin_session, started["job_id"])
        result = finished.get("result") or {}
        assert result.get("status") == "success", finished
        assert result.get("record_count", 0) > 0, finished
        assert result.get("silver_refresh", {}).get("result", {}).get("refreshed", 0) >= 1, finished
        storage_uri = result.get("storage_uri") or ""
        assert storage_uri.startswith(f"s3://lakehouse/raw/hubspot/{entity}/"), storage_uri
        assert "/tenant_id=" in storage_uri and "/workspace_id=" in storage_uri, storage_uri
        assert _host_file_for_s3_uri(storage_uri).exists(), storage_uri
        extracted[entity] = result

    for entity, result in extracted.items():
        db_count = _psql_scalar(
            "SELECT COALESCE(MAX(records_extracted), 0) "
            f"FROM extraction_runs WHERE cartridge_id='hubspot' AND entity_name='{entity}' AND status='success';"
        )
        assert int(db_count or "0") >= result["record_count"]

    freshness = admin_session.get("/api/freshness/hubspot")
    assert freshness.status_code == 200, freshness.text
    by_entity = {item["entity"]: item for item in freshness.json().get("entities", [])}
    for entity in extracted:
        assert by_entity[entity]["last_run_status"] == "success", by_entity[entity]

    preview_response = admin_session.post(
        "/api/mcp/servers/hubspot/invoke",
        json={"tool": "preview", "args": {"entity": "deals", "limit": 10}},
        headers=_csrf(admin_session),
        timeout=30,
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview.get("count", 0) >= 2, preview
    assert any(row.get("dealname") == "ACME Expansion" for row in preview.get("rows", []))


def test_04_gold_catalog_and_data_query_work_end_to_end(admin_session: httpx.Client):
    refresh = admin_session.post(
        "/datasets/pipeline_salud/refresh",
        json={},
        headers=_csrf(admin_session),
        timeout=120,
    )
    assert refresh.status_code == 200, refresh.text
    refreshed = refresh.json()
    assert refreshed.get("name") == "pipeline_salud", refreshed
    assert refreshed.get("layer") == "gold", refreshed
    assert refreshed.get("row_count", 0) >= 2, refreshed

    rows_response = admin_session.get("/api/data/pipeline_salud?limit=20", timeout=60)
    assert rows_response.status_code == 200, rows_response.text
    rows = rows_response.json()
    assert isinstance(rows, list), rows
    acme = next((row for row in rows if row.get("dealname") == "ACME Expansion"), None)
    assert acme, rows
    assert acme["vendedor"] == "*ofia *ales"
    assert float(acme["monto_ponderado_usd"]) == pytest.approx(3000.0)

    catalog = admin_session.get("/api/catalog?cartridge=hubspot", timeout=60)
    assert catalog.status_code == 200, catalog.text
    assert "pipeline_salud" in catalog.text
    assert "monto_ponderado_usd" in catalog.text

    lineage = admin_session.get("/api/datasets/pipeline_salud/lineage")
    assert lineage.status_code == 200, lineage.text
    assert "hubspot_deals_latest" in lineage.text or "pipeline_salud" in lineage.text
