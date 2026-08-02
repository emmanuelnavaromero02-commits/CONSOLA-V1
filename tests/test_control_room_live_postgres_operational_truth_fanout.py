"""Real PostgreSQL proof for scheduled workspace fan-out and replay slots."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

import asyncpg
import pytest

from app.services import agent_scheduler, auth, scheduled_runtime
from airflow.dags.dataset_refresh_idempotency import (
    finish_materialization,
    reserve_materialization,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)
from tests.test_control_room_live_postgres_operational_truth_pipeline import (
    _seed_scope,
)


@pytest.mark.asyncio
async def test_live_fanout_scopes_each_workspace_and_replay_is_durable(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
):
    setup = await asyncpg.connect(postgres_with_real_init_schema)
    agent_a = str(uuid.uuid4())
    agent_b = str(uuid.uuid4())
    try:
        scope_a = await _seed_scope(setup, "fanout-a")
        scope_b = await _seed_scope(setup, "fanout-b")
        for agent_id, scope, slug in (
            (agent_a, scope_a, "operational-truth-a"),
            (agent_b, scope_b, "operational-truth-b"),
        ):
            await setup.execute(
                """
                INSERT INTO agents (
                    id, cartridge_id, slug, name, is_active, tenant_id,
                    workspace_id, extra
                )
                VALUES (
                    $1, 'replicon', $2, $2, TRUE, $3, $4,
                    '{"schedule":{"enabled":true,"cron":"2 12 * * *","tz":"UTC"}}'
                )
                """,
                agent_id,
                slug,
                scope["tenant_id"],
                scope["workspace_id"],
            )
    finally:
        await setup.close()

    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=4)
    pool_owners = {id(module): module for module in (auth, agent_scheduler.auth)}
    previous_factories = [(module, module.pool) for module in pool_owners.values()]

    async def active_pool():
        return pool

    for module in pool_owners.values():
        module.pool = active_pool
    fire_at = datetime(2026, 7, 30, 12, 2, tzinfo=timezone.utc)
    try:
        discovery = await scheduled_runtime.find_due_agents(
            pool,
            window_start=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 7, 30, 12, 5, tzinfo=timezone.utc),
        )
        due = {row["id"]: row for row in discovery["due"]}
        first_a = await agent_scheduler.reserve_scheduled_run(
            agent_id=agent_a,
            tenant_id=scope_a["tenant_id"],
            workspace_id=scope_a["workspace_id"],
            scheduled_fire_at=fire_at,
        )
        replay_a = await agent_scheduler.reserve_scheduled_run(
            agent_id=agent_a,
            tenant_id=scope_a["tenant_id"],
            workspace_id=scope_a["workspace_id"],
            scheduled_fire_at=fire_at,
        )
        first_b = await agent_scheduler.reserve_scheduled_run(
            agent_id=agent_b,
            tenant_id=scope_b["tenant_id"],
            workspace_id=scope_b["workspace_id"],
            scheduled_fire_at=fire_at,
        )
        materialize_a = reserve_materialization(
            omega_console_live_dsn,
            airflow_run_id="operational-truth-replay",
            tenant_id=scope_a["tenant_id"],
            workspace_id=scope_a["workspace_id"],
            cartridge_id="replicon",
            dataset="pnl_mensual",
        )
        finish_materialization(
            omega_console_live_dsn,
            slot_id=materialize_a["slot_id"],
            tenant_id=scope_a["tenant_id"],
            workspace_id=scope_a["workspace_id"],
            lease_token=materialize_a["lease_token"],
            success=True,
            result={"name": "pnl_mensual", "layer": "gold", "row_count": 3},
        )
        materialize_a_replay = reserve_materialization(
            omega_console_live_dsn,
            airflow_run_id="operational-truth-replay",
            tenant_id=scope_a["tenant_id"],
            workspace_id=scope_a["workspace_id"],
            cartridge_id="replicon",
            dataset="pnl_mensual",
        )
        materialize_b = reserve_materialization(
            omega_console_live_dsn,
            airflow_run_id="operational-truth-replay",
            tenant_id=scope_b["tenant_id"],
            workspace_id=scope_b["workspace_id"],
            cartridge_id="replicon",
            dataset="pnl_mensual",
        )
    finally:
        for module, previous_factory in previous_factories:
            module.pool = previous_factory
        await pool.close()

    assert discovery["status"] == "ready"
    assert due[agent_a]["workspace_id"] == scope_a["workspace_id"]
    assert due[agent_b]["workspace_id"] == scope_b["workspace_id"]
    assert first_a["reserved"] is True
    assert replay_a["duplicate"] is True
    assert replay_a["id"] == first_a["id"]
    assert first_b["reserved"] is True
    assert first_b["id"] != first_a["id"]
    assert materialize_a_replay["completed"] is True
    assert materialize_a_replay["result"]["row_count"] == 3
    assert materialize_a_replay["result"]["layer"] == "gold"
    assert materialize_b["slot_id"] != materialize_a["slot_id"]
