from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import asyncpg
import pytest
from fastapi import HTTPException

from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


MONITOR_EXTRA = {
    "role": "monitor",
    "monitor": {"wisdom_bit_id": "WB-TALENTO", "domain": "Recursos Humanos"},
    "schedule": {"cron": "0 7 * * *"},
}


async def _seed(admin: asyncpg.Connection) -> dict[str, str]:
    suffix = uuid.uuid4().hex[:12]
    tenant = str(
        await admin.fetchval(
            "INSERT INTO tenants (name, slug) VALUES ($1, $1) RETURNING id",
            f"refresh-monitors-{suffix}",
        )
    )
    workspaces = [
        str(
            await admin.fetchval(
                "INSERT INTO workspaces (tenant_id, name) VALUES ($1::uuid, $2) RETURNING id",
                tenant,
                f"Refresh monitors {label} {suffix}",
            )
        )
        for label in ("A", "B")
    ]
    ids = {"tenant": tenant, "ws_a": workspaces[0], "ws_b": workspaces[1], "suffix": suffix}
    for key, workspace, slug, extra in (
        ("monitor_a", workspaces[0], f"talent_monitor_{suffix}", MONITOR_EXTRA),
        ("advisor_a", workspaces[0], f"advisor_{suffix}", {"role": "advisor"}),
        ("monitor_b", workspaces[1], f"talent_monitor_{suffix}", MONITOR_EXTRA),
    ):
        ids[key] = str(uuid.uuid4())
        await admin.execute(
            """INSERT INTO agents (
                   id, cartridge_id, slug, name, is_active, tenant_id, workspace_id, extra
               ) VALUES ($1, 'sap_successfactors', $2, $2, TRUE, $3, $4, $5::jsonb)""",
            ids[key],
            slug,
            tenant,
            workspace,
            json.dumps(extra),
        )
    for key, workspace, status in (
        ("ok_a", workspaces[0], "success"),
        ("failed_a", workspaces[0], "failed"),
        ("ok_b", workspaces[1], "success"),
    ):
        ids[key] = f"dataset_refresh_chain:successfactors__dataset_refresh__{key}_{suffix}"
        await admin.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, dag_id, cartridge_id, entity, airflow_dag_run_id, mode, status,
                started_at, finished_at, tenant_id, workspace_id
            ) VALUES ($1, 'dataset_refresh_chain', 'sap_successfactors', 'DatasetRefreshChain',
                      $2, 'refresh', $3, NOW(), NOW(), $4::uuid, $5::uuid)
            """,
            ids[key],
            ids[key].split(":", 1)[1],
            status,
            tenant,
            workspace,
        )
    return ids


async def _cleanup(admin: asyncpg.Connection, ids: dict[str, str]) -> None:
    await admin.execute(
        "DELETE FROM pipeline_runs WHERE workspace_id = ANY($1::uuid[])",
        [ids["ws_a"], ids["ws_b"]],
    )
    await admin.execute(
        "DELETE FROM workspaces WHERE id = ANY($1::uuid[])", [ids["ws_a"], ids["ws_b"]]
    )
    await admin.execute("DELETE FROM tenants WHERE id = $1::uuid", ids["tenant"])


@pytest.mark.asyncio
async def test_monitors_run_once_per_published_refresh_in_their_own_workspace(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    from app.services import agent_runtime, agent_scheduler, auth, scheduled_automation

    admin = await asyncpg.connect(postgres_with_real_init_schema)
    ids = await _seed(admin)
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=3)

    async def active_pool():
        return pool

    previous_pools = [(module, module.pool) for module in {id(m): m for m in (auth, agent_scheduler.auth)}.values()]
    previous_runtime_pool = agent_runtime._pool
    for module, _ in previous_pools:
        module.pool = active_pool
    agent_runtime._pool = pool
    try:
        due = await scheduled_automation.refresh_monitors_due(
            pool,
            tenant_id=ids["tenant"],
            workspace_id=ids["ws_a"],
            refresh_run_id=ids["ok_a"],
            ensure_monitors=AsyncMock(),
        )
        assert [item["id"] for item in due["due"]] == [ids["monitor_a"]]
        assert due["cartridge_id"] == "sap_successfactors"

        for run_id in (ids["failed_a"], ids["ok_b"]):
            with pytest.raises(HTTPException) as error:
                await scheduled_automation.refresh_monitors_due(
                    pool,
                    tenant_id=ids["tenant"],
                    workspace_id=ids["ws_a"],
                    refresh_run_id=run_id,
                    ensure_monitors=AsyncMock(),
                )
            assert error.value.status_code == 409

        execute = AsyncMock(
            return_value={
                "run_id": 1,
                "deterministic_monitor": True,
                "monitor": {"status": "ok", "signals": 0, "blockers": 0, "engines": [], "alerted": False},
            }
        )
        first = await scheduled_automation.invoke_refresh_monitor(
            pool,
            agent_id=ids["monitor_a"],
            tenant_id=ids["tenant"],
            workspace_id=ids["ws_a"],
            refresh_run_id=ids["ok_a"],
            airflow_dag_run_id=ids["ok_a"].split(":", 1)[1],
            execute_reserved_scheduled_monitor=execute,
        )
        assert first["ok"] is True and first["duplicate"] is False
        second = await scheduled_automation.invoke_refresh_monitor(
            pool,
            agent_id=ids["monitor_a"],
            tenant_id=ids["tenant"],
            workspace_id=ids["ws_a"],
            refresh_run_id=ids["ok_a"],
            airflow_dag_run_id=ids["ok_a"].split(":", 1)[1],
            execute_reserved_scheduled_monitor=execute,
        )
        assert second["duplicate"] is True
        assert execute.await_count == 1

        rows = await admin.fetch(
            "SELECT schedule_key, scheduled_fire_at, metadata->>'source' AS source "
            "FROM agent_schedule_runs WHERE agent_id = $1::uuid",
            ids["monitor_a"],
        )
        assert len(rows) == 1
        assert rows[0]["schedule_key"] == scheduled_automation.refresh_schedule_key(ids["ok_a"])
        assert rows[0]["scheduled_fire_at"] == datetime(1970, 1, 1, tzinfo=timezone.utc)
        assert rows[0]["source"] == "after-refresh"

        with pytest.raises(HTTPException) as error:
            await scheduled_automation.invoke_refresh_monitor(
                pool,
                agent_id=ids["monitor_b"],
                tenant_id=ids["tenant"],
                workspace_id=ids["ws_a"],
                refresh_run_id=ids["ok_a"],
                airflow_dag_run_id=None,
                execute_reserved_scheduled_monitor=execute,
            )
        assert error.value.status_code in {403, 404}
        assert execute.await_count == 1
    finally:
        for module, original in previous_pools:
            module.pool = original
        agent_runtime._pool = previous_runtime_pool
        await pool.close()
        await _cleanup(admin, ids)
        await admin.close()
