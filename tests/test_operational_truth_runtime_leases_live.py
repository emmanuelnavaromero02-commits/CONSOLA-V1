from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
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
from console.app.routers import intelligence as intelligence_router
from console.app.services import agent_scheduler, auth
from console.app.services.security_context import (
    sign_runtime_envelope,
    sign_security_context,
)
from console.app.services.scheduled_monitor_execution import (
    ScheduledMonitorTimedOut,
    execute_reserved_scheduled_monitor,
)
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
            result={"name": "employees", "layer": "silver", "row_count": 1},
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
            result={"name": "employees", "layer": "silver", "row_count": 1},
        )
    finally:
        for module, factory in previous:
            module.pool = factory
        await pool.close()
        await admin.close()


@pytest.mark.asyncio
async def test_timeout_retires_real_postgres_fence_before_rebel_effect(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await asyncpg.connect(postgres_with_real_init_schema)
    scope = await _seed_scope(admin, "timeout-fence")
    agent_id = str(uuid.uuid4())
    await admin.execute(
        """INSERT INTO agents(
               id,cartridge_id,slug,name,is_active,tenant_id,workspace_id,extra
           ) VALUES($1,'replicon',$2,$2,TRUE,$3,$4,'{}')""",
        agent_id,
        f"timeout-fence-{uuid.uuid4().hex}",
        scope["tenant_id"],
        scope["workspace_id"],
    )
    pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=3)
    pool_owners = {id(module): module for module in (auth, agent_scheduler.auth)}
    previous = [(module, module.pool) for module in pool_owners.values()]

    async def active_pool():
        return pool

    for module in pool_owners.values():
        module.pool = active_pool
    try:
        reservation = await agent_scheduler.reserve_scheduled_run(
            agent_id=agent_id,
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            scheduled_fire_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
            lease_seconds=30,
        )

        async def rebel(*_args, schedule_run_id, fencing_token, **_kwargs):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                try:
                    async with pool.acquire() as connection:
                        async with connection.transaction():
                            await connection.execute(
                                "SELECT set_config('app.tenant_id',$1,true),"
                                "set_config('app.workspace_id',$2,true)",
                                scope["tenant_id"],
                                scope["workspace_id"],
                            )
                            await connection.execute(
                                "SELECT assert_scheduled_effect_authority("
                                "$1,$2,$3::uuid,$4::uuid,$5::uuid)",
                                schedule_run_id,
                                fencing_token,
                                scope["tenant_id"],
                                scope["workspace_id"],
                                agent_id,
                            )
                            await connection.execute(
                                "UPDATE agent_schedule_runs SET metadata="
                                "metadata || '{\"rebel_effect\":true}'::jsonb "
                                "WHERE id=$1",
                                schedule_run_id,
                            )
                except asyncpg.PostgresError:
                    pass
                return {"run_id": 1, "deterministic_monitor": True, "monitor": {}}

        with pytest.raises(ScheduledMonitorTimedOut):
            await execute_reserved_scheduled_monitor(
                agent=SimpleNamespace(
                    id=agent_id,
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                ),
                message="probe",
                reservation=reservation,
                scheduled_fire_at=None,
                lease_seconds=30,
                heartbeat_interval_seconds=0.01,
                execution_timeout_seconds=0.03,
                run_scheduled_monitor=rebel,
                heartbeat_scheduled_run=agent_scheduler.heartbeat_scheduled_run,
                finish_scheduled_run=agent_scheduler.finish_scheduled_run,
            )
        row = await admin.fetchrow(
            "SELECT status,metadata ? 'rebel_effect' AS rebel_effect "
            "FROM agent_schedule_runs WHERE id=$1",
            reservation["id"],
        )
        assert dict(row) == {"status": "error", "rebel_effect": False}

        external = await agent_scheduler.reserve_scheduled_run(
            agent_id=agent_id,
            tenant_id=scope["tenant_id"],
            workspace_id=scope["workspace_id"],
            scheduled_fire_at=datetime(2026, 8, 2, 0, 1, tzinfo=timezone.utc),
            lease_seconds=30,
        )
        external_started = asyncio.Event()
        close_attempts = 0

        async def external_rebel(*args, **kwargs):
            external_started.set()
            return await rebel(*args, **kwargs)

        async def flaky_finish(**kwargs):
            nonlocal close_attempts
            close_attempts += 1
            if close_attempts == 1:
                raise OSError("injected close transport failure")
            return await agent_scheduler.finish_scheduled_run(**kwargs)

        external_task = asyncio.create_task(
            execute_reserved_scheduled_monitor(
                agent=SimpleNamespace(
                    id=agent_id,
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                ),
                message="probe",
                reservation=external,
                scheduled_fire_at=None,
                lease_seconds=30,
                heartbeat_interval_seconds=0.01,
                execution_timeout_seconds=1,
                run_scheduled_monitor=external_rebel,
                heartbeat_scheduled_run=agent_scheduler.heartbeat_scheduled_run,
                finish_scheduled_run=flaky_finish,
            )
        )
        await asyncio.wait_for(external_started.wait(), timeout=0.3)
        external_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(external_task, timeout=0.5)
        external_row = await admin.fetchrow(
            "SELECT status,metadata ? 'rebel_effect' AS rebel_effect "
            "FROM agent_schedule_runs WHERE id=$1",
            external["id"],
        )
        assert close_attempts == 2
        assert dict(external_row) == {"status": "cancelled", "rebel_effect": False}

        monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "remote-fence-" + "x" * 52)
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("DECISION_ORCHESTRATOR_CREATE_ACTIONS", "false")
        security_context = sign_security_context(
            {
                "trusted": True,
                "source": "agent_runner",
                "user_id": None,
                "email": "agent-runner@omega.local",
                "role": "agent",
                "workspace_role": "workspace_admin",
                "permissions": ["control_room.write"],
                "tenant_id": scope["tenant_id"],
                "workspace_id": scope["workspace_id"],
                "agent_id": agent_id,
                "agent_run_id": None,
            }
        )
        cases = (
            (
                "simulation__monte_carlo_run",
                "monte_carlo_simulations",
                {
                    "source_type": "manual_fixture",
                    "source_id": "remote-fence-monte-carlo",
                    "iterations": 20,
                    "seed": 7,
                    "input_variables": {
                        "baseline_value": {"type": "fixed", "value": 100},
                        "expected_delta": {"type": "fixed", "value": 5},
                    },
                },
                intelligence_router.intelligence_monte_carlo_run_internal,
            ),
            (
                "decision__orchestrate",
                "decision_orchestration_runs",
                {
                    "source_type": "manual_fixture",
                    "source_id": "remote-fence-decision",
                    "title": "Remote fencing decision",
                    "metrics": {"risk_metric": "headcount"},
                },
                intelligence_router.intelligence_orchestrate_internal,
            ),
        )
        for offset, (tool, table, payload, route) in enumerate(cases, start=2):
            remote = await agent_scheduler.reserve_scheduled_run(
                agent_id=agent_id,
                tenant_id=scope["tenant_id"],
                workspace_id=scope["workspace_id"],
                scheduled_fire_at=datetime(2026, 8, 2, 0, offset, tzinfo=timezone.utc),
                lease_seconds=30,
            )
            authority = sign_runtime_envelope(
                {
                    "source": "console",
                    "audience": "mcp-infra",
                    "purpose": "mcp.scheduled_effect",
                    "tool": tool,
                    "schedule_run_id": remote["id"],
                    "fencing_token": remote["fencing_token"],
                    "tenant_id": scope["tenant_id"],
                    "workspace_id": scope["workspace_id"],
                    "agent_id": agent_id,
                    "agent_run_id": None,
                    "jti": uuid.uuid4().hex + uuid.uuid4().hex,
                    "body_digest": uuid.uuid4().hex + uuid.uuid4().hex,
                }
            )
            body_type = (
                intelligence_router.InternalMcpDecisionRequest
                if tool == "decision__orchestrate"
                else intelligence_router.InternalMcpRequest
            )
            body = body_type(
                security_context=security_context,
                payload=payload,
                effect_authority=authority,
            )
            blocker = await asyncpg.connect(postgres_with_real_init_schema)
            blocking_tx = blocker.transaction()
            await blocking_tx.start()
            await blocker.execute(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE")
            route_task = asyncio.create_task(route(body, internal_service="mcp-infra"))
            for _ in range(200):
                sink_blocked = await admin.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                    "WHERE cardinality(pg_blocking_pids(pid)) > 0 "
                    "AND query LIKE $1)",
                    f"%INSERT INTO {table}%",
                )
                if sink_blocked:
                    break
                await asyncio.sleep(0)
            assert sink_blocked is True
            retire_task = asyncio.create_task(
                agent_scheduler.finish_scheduled_run(
                    schedule_run_id=remote["id"],
                    agent_run_id=None,
                    status="error",
                    tenant_id=scope["tenant_id"],
                    workspace_id=scope["workspace_id"],
                    fencing_token=remote["fencing_token"],
                )
            )
            for _ in range(200):
                retire_blocked = await admin.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                    "WHERE cardinality(pg_blocking_pids(pid)) > 0 "
                    "AND query LIKE 'SELECT pg_advisory_xact_lock%')"
                )
                if retire_blocked:
                    break
                await asyncio.sleep(0)
            assert retire_blocked is True
            assert route_task.done() is False
            assert retire_task.done() is False
            await blocking_tx.commit()
            await blocker.close()
            result = await asyncio.wait_for(route_task, timeout=3)
            await asyncio.wait_for(retire_task, timeout=3)
            assert "effect_authority" not in str(result)
            assert authority["_signature"] not in str(result)
            assert authority["jti"] not in str(result)
            assert (
                await admin.fetchval(
                    f"SELECT count(*) FROM {table} WHERE workspace_id=$1 AND source_id=$2",
                    scope["workspace_id"],
                    payload["source_id"],
                )
                == 1
            )
            with pytest.raises(Exception, match="stale"):
                await route(body, internal_service="mcp-infra")
            missing = body_type(security_context=security_context, payload=payload)
            with pytest.raises(Exception, match="required"):
                await route(missing, internal_service="mcp-infra")
            assert (
                await admin.fetchval(
                    f"SELECT count(*) FROM {table} WHERE workspace_id=$1 AND source_id=$2",
                    scope["workspace_id"],
                    payload["source_id"],
                )
                == 1
            )
    finally:
        for module, factory in previous:
            module.pool = factory
        await pool.close()
        await admin.close()
