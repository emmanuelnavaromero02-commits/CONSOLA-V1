from __future__ import annotations

from datetime import datetime, timezone
import uuid

import asyncpg
import psycopg2
import pytest

from airflow.dags.dataset_refresh_idempotency import (
    finish_materialization,
    heartbeat_materialization,
    reserve_materialization,
)
from airflow.dags.runtime_security_context import build_materialize_context
from console.app.services import agent_scheduler, auth
from refinement.app.runtime_security_context import validate_runtime_context
from tests.test_control_room_live_postgres_operational_truth_pipeline import _seed_scope
from tests.test_operational_rls_console_refinement import (
    OMEGA_REFINEMENT_PASSWORD,
    POSTGRES_PASSWORD,
    POSTGRES_USER,
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


def _refinement_dsn(admin_dsn: str) -> str:
    return admin_dsn.replace(
        f"{POSTGRES_USER}:{POSTGRES_PASSWORD}",
        f"omega_refinement:{OMEGA_REFINEMENT_PASSWORD}",
    )


def _seed(admin_dsn: str, suffix: str) -> dict[str, str]:
    async def create() -> dict[str, str]:
        connection = await asyncpg.connect(admin_dsn)
        try:
            return await _seed_scope(connection, suffix)
        finally:
            await connection.close()

    import asyncio

    return asyncio.run(create())


def test_hmac_v2_jti_is_one_use_and_rls_scoped(
    monkeypatch: pytest.MonkeyPatch,
    postgres_with_real_init_schema: str,
) -> None:
    scope_a = _seed(postgres_with_real_init_schema, "hmac-a")
    scope_b = _seed(postgres_with_real_init_schema, "hmac-b")
    dsn = _refinement_dsn(postgres_with_real_init_schema)
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setenv(
        "SECURITY_CONTEXT_SIGNING_KEY",
        "runtime-signing-key-that-is-long-and-isolated-123456",
    )
    body = {"tool": "materialize", "args": {"name": "employees"}}
    context = build_materialize_context(
        tenant_id=scope_a["tenant_id"],
        workspace_id=scope_a["workspace_id"],
        cartridge_id="replicon",
        dataset_name="employees",
        run_id="scheduled__hmac-replay",
        now=1000,
    )
    validate_runtime_context(
        context, body=body, internal_service="airflow", now=1000, consume=True
    )
    with pytest.raises(ValueError, match="replay"):
        validate_runtime_context(
            context, body=body, internal_service="airflow", now=1000, consume=True
        )
    fresh = build_materialize_context(
        tenant_id=scope_a["tenant_id"],
        workspace_id=scope_a["workspace_id"],
        cartridge_id="replicon",
        dataset_name="employees",
        run_id="scheduled__hmac-replay",
        now=1000,
    )
    validate_runtime_context(
        fresh, body=body, internal_service="airflow", now=1000, consume=True
    )
    with psycopg2.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.tenant_id',%s,true)", (scope_b["tenant_id"],)
        )
        cursor.execute(
            "SELECT set_config('app.workspace_id',%s,true)",
            (scope_b["workspace_id"],),
        )
        cursor.execute("SELECT count(*) FROM runtime_hmac_nonces")
        assert cursor.fetchone()[0] == 0


@pytest.mark.asyncio
async def test_expired_leases_reclaim_and_old_fencing_cannot_finish(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    scope = await _seed_scope(admin, "lease")
    agent_id = str(uuid.uuid4())
    await admin.execute(
        """INSERT INTO agents (
               id,cartridge_id,slug,name,is_active,tenant_id,workspace_id,extra
           ) VALUES ($1,'replicon',$2,$2,TRUE,$3,$4,'{}')""",
        agent_id,
        f"lease-{uuid.uuid4().hex}",
        scope["tenant_id"],
        scope["workspace_id"],
    )
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=2)
    pool_owners = {id(module): module for module in (auth, agent_scheduler.auth)}
    previous = [(module, module.pool) for module in pool_owners.values()]

    async def active_pool():
        return pool

    for module in pool_owners.values():
        module.pool = active_pool
    try:
        fire = datetime(2026, 8, 1, tzinfo=timezone.utc)
        first = await agent_scheduler.reserve_scheduled_run(
            agent_id=agent_id,
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            scheduled_fire_at=fire,
        )
        active = await agent_scheduler.reserve_scheduled_run(
            agent_id=agent_id,
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            scheduled_fire_at=fire,
        )
        assert active["duplicate"] is True
        await admin.execute(
            "UPDATE agent_schedule_runs SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE id=$1",
            first["id"],
        )
        with pytest.raises(RuntimeError, match="reservation"):
            await agent_scheduler.finish_scheduled_run(
                schedule_run_id=first["id"],
                agent_run_id=None,
                status="error",
                tenant_id=scope["tenant_id"],
                workspace_id=scope["workspace_id"],
                fencing_token=first["fencing_token"],
            )
        reclaimed = await agent_scheduler.reserve_scheduled_run(
            agent_id=agent_id,
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            scheduled_fire_at=fire,
        )
        with pytest.raises(RuntimeError, match="lease"):
            await agent_scheduler.heartbeat_scheduled_run(
                schedule_run_id=first["id"],
                tenant_id=scope["tenant_id"],
                workspace_id=scope["workspace_id"],
                fencing_token=first["fencing_token"],
            )
        with pytest.raises(RuntimeError, match="reservation"):
            await agent_scheduler.finish_scheduled_run(
                schedule_run_id=first["id"],
                agent_run_id=None,
                status="error",
                tenant_id=scope["tenant_id"],
                workspace_id=scope["workspace_id"],
                fencing_token=first["fencing_token"],
            )
        await agent_scheduler.finish_scheduled_run(
            schedule_run_id=reclaimed["id"],
            agent_run_id=None,
            status="ok",
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            fencing_token=reclaimed["fencing_token"],
        )

        slot = reserve_materialization(
            omega_console_live_dsn,
            airflow_run_id="lease-run",
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            cartridge_id="replicon",
            dataset="employees",
        )
        with psycopg2.connect(postgres_with_real_init_schema) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE pipeline_runs SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE run_id=%s",
                    (slot["slot_id"],),
                )
        with pytest.raises(RuntimeError, match="slot"):
            finish_materialization(
                omega_console_live_dsn,
                slot_id=slot["slot_id"],
                tenant_id=scope["tenant_id"],
                workspace_id=scope["workspace_id"],
                lease_token=slot["lease_token"],
                success=False,
            )
        next_slot = reserve_materialization(
            omega_console_live_dsn,
            airflow_run_id="lease-run",
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            cartridge_id="replicon",
            dataset="employees",
        )
        with pytest.raises(RuntimeError, match="slot"):
            finish_materialization(
                omega_console_live_dsn,
                slot_id=slot["slot_id"],
                tenant_id=scope["tenant_id"],
                workspace_id=scope["workspace_id"],
                lease_token=slot["lease_token"],
                success=False,
            )
        finish_materialization(
            omega_console_live_dsn,
            slot_id=next_slot["slot_id"],
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            lease_token=next_slot["lease_token"],
            success=True,
            result={"name": "employees", "row_count": 1},
        )

        healthy_fire = fire.replace(minute=1)
        healthy_run = await agent_scheduler.reserve_scheduled_run(
            agent_id=agent_id,
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            scheduled_fire_at=healthy_fire,
        )
        await agent_scheduler.heartbeat_scheduled_run(
            schedule_run_id=healthy_run["id"],
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            fencing_token=healthy_run["fencing_token"],
        )
        await agent_scheduler.finish_scheduled_run(
            schedule_run_id=healthy_run["id"],
            agent_run_id=None,
            status="ok",
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            fencing_token=healthy_run["fencing_token"],
        )

        healthy_slot = reserve_materialization(
            omega_console_live_dsn,
            airflow_run_id="lease-run-heartbeat",
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            cartridge_id="replicon",
            dataset="employees",
        )
        heartbeat_materialization(
            omega_console_live_dsn,
            slot_id=healthy_slot["slot_id"],
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            lease_token=healthy_slot["lease_token"],
        )
        finish_materialization(
            omega_console_live_dsn,
            slot_id=healthy_slot["slot_id"],
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            lease_token=healthy_slot["lease_token"],
            success=True,
            result={"name": "employees", "row_count": 1},
        )
    finally:
        for module, factory in previous:
            module.pool = factory
        await pool.close()
        await admin.close()
