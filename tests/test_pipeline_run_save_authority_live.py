from __future__ import annotations

import importlib
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import pytest
from fastapi.testclient import TestClient

from tests.test_operational_rls_console_refinement import (
    POSTGRES_PASSWORD,
    POSTGRES_USER,
    postgres_with_real_init_schema,
)


ROOT = Path(__file__).resolve().parents[1]
MCP_PASSWORD = "test_omega_mcp_infra_password"


def _purge_app() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


async def _scope(admin_dsn: str) -> tuple[str, str]:
    tenant = str(uuid.uuid4())
    workspace = str(uuid.uuid4())
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(
            "INSERT INTO tenants(id,name,slug,status) VALUES($1,$2,$3,'active')",
            tenant,
            f"Pipeline {tenant[:8]}",
            f"pipeline-{tenant[:8]}",
        )
        await conn.execute(
            "INSERT INTO workspaces(id,tenant_id,name) VALUES($1,$2,$3)",
            workspace,
            tenant,
            f"Pipeline {workspace[:8]}",
        )
    finally:
        await conn.close()
    return tenant, workspace


@pytest.mark.asyncio
async def test_unsigned_airflow_http_cannot_write_chosen_workspace(
    postgres_with_real_init_schema: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, workspace = await _scope(postgres_with_real_init_schema)
    tenant_b, workspace_b = await _scope(postgres_with_real_init_schema)
    parsed = urlsplit(postgres_with_real_init_schema)
    _purge_app()
    monkeypatch.syspath_prepend(str(ROOT / "mcp-infra"))
    monkeypatch.syspath_prepend(str(ROOT / "airflow" / "dags"))
    for name, value in {
        "APP_ENV": "test",
        "INTERNAL_API_KEY": "transport-key-that-is-long-enough-123456",
        "INTERNAL_API_KEY_AIRFLOW_TO_MCP_INFRA": "airflow-pair-key-123456",
        "SECURITY_CONTEXT_SIGNING_KEY": "signing-key-distinct-and-long-enough-123456",
        "PG_HOST": parsed.hostname or "127.0.0.1",
        "PG_PORT": str(parsed.port),
        "PG_DB": parsed.path.lstrip("/"),
        "PG_USER": "omega_mcp_infra",
        "PG_PASSWORD": MCP_PASSWORD,
        "AIRFLOW_USER": "airflow",
        "AIRFLOW_PASSWORD": "airflow-password",
        "SUPERSET_USER": "superset",
        "SUPERSET_PASSWORD": "superset-password",
    }.items():
        monkeypatch.setenv(name, value)
    main = importlib.import_module("app.main")
    from runtime_security_context import build_pipeline_run_context

    run_id = f"scheduled__unsigned-{uuid.uuid4().hex}"
    args = {
        "run_id": run_id,
        "dag_id": "dataset_refresh_chain",
        "cartridge_id": "replicon",
        "entity": "employees",
        "status": "success",
        "tenant_id": tenant,
        "workspace_id": workspace,
    }
    try:
        response = TestClient(main.app).post(
            "/mcp/invoke",
            headers={
                "X-Internal-Service": "airflow",
                "X-API-Key": "airflow-pair-key-123456",
            },
            json={
                "tool": "pipeline_run_save",
                "args": args,
            },
        )
        assert response.status_code == 403
        admin = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM pipeline_runs WHERE run_id=$1", run_id
                )
                == 0
            )
        finally:
            await admin.close()

        envelope = {
            "tool": "pipeline_run_save",
            "args": args,
            "security_context": build_pipeline_run_context(args),
        }
        client = TestClient(main.app)
        headers = {
            "X-Internal-Service": "airflow",
            "X-API-Key": "airflow-pair-key-123456",
        }
        assert (
            client.post("/mcp/invoke", headers=headers, json=envelope).status_code
            == 200
        )
        assert (
            client.post("/mcp/invoke", headers=headers, json=envelope).status_code
            == 403
        )
        envelope["security_context"] = build_pipeline_run_context(args)
        assert (
            client.post("/mcp/invoke", headers=headers, json=envelope).status_code
            == 200
        )
        conflicting_args = {
            **args,
            "tenant_id": tenant_b,
            "workspace_id": workspace_b,
        }
        conflict = client.post(
            "/mcp/invoke",
            headers=headers,
            json={
                "tool": "pipeline_run_save",
                "args": conflicting_args,
                "security_context": build_pipeline_run_context(conflicting_args),
            },
        )
        assert conflict.status_code == 500
        admin = await asyncpg.connect(postgres_with_real_init_schema)
        try:
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM pipeline_runs WHERE run_id=$1", run_id
                )
                == 1
            )
            bound = await admin.fetchrow(
                "SELECT tenant_id::text,workspace_id::text "
                "FROM pipeline_runs WHERE run_id=$1",
                run_id,
            )
            assert tuple(bound.values()) == (tenant, workspace)
            assert (
                await admin.fetchval(
                    "SELECT count(*) FROM runtime_hmac_nonces WHERE run_id=$1 "
                    "AND purpose='mcp.pipeline_run_save'",
                    run_id,
                )
                == 3
            )
        finally:
            await admin.close()
    finally:
        _purge_app()
