from __future__ import annotations

import asyncio
import importlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)



DAG_ID = "sap_successfactors_extract"


def _service():
    return importlib.import_module("app.domains.pipeline.stuck_run_recovery_service")


async def _workspace(admin_dsn: str) -> tuple[str, str]:
    tenant = str(uuid.uuid4())
    workspace = str(uuid.uuid4())
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(
            "INSERT INTO tenants(id,name,slug,status) VALUES($1,$2,$3,'active')",
            tenant,
            f"Recovery {tenant[:8]}",
            f"recovery-{tenant[:8]}",
        )
        await conn.execute(
            "INSERT INTO workspaces(id,tenant_id,name) VALUES($1,$2,$3)",
            workspace,
            tenant,
            f"Recovery {workspace[:8]}",
        )
    finally:
        await conn.close()
    return tenant, workspace


async def _insert_run(
    admin_dsn: str,
    *,
    tenant: str,
    workspace: str,
    status: str = "queued",
    age: timedelta = timedelta(hours=3),
    error_message: str | None = "trigger accepted",
    cartridge: str = "sap_successfactors",
    dag_id: str = DAG_ID,
) -> str:
    run_id = f"manual__recovery-{uuid.uuid4().hex}"
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(
            """
            INSERT INTO pipeline_runs (
                run_id, dag_id, cartridge_id, entity, airflow_dag_run_id, mode,
                status, started_at, error_message, extra, tenant_id, workspace_id
            )
            VALUES ($1, $2, $8, 'User', $1, 'incremental',
                    $3, NOW() - $4::interval, $5, '{"reserved": true}'::jsonb,
                    $6::uuid, $7::uuid)
            """,
            run_id,
            dag_id,
            status,
            age,
            error_message,
            tenant,
            workspace,
            cartridge,
        )
    finally:
        await conn.close()
    return run_id


async def _row(admin_dsn: str, run_id: str) -> dict:
    conn = await asyncpg.connect(admin_dsn)
    try:
        row = await conn.fetchrow("SELECT * FROM pipeline_runs WHERE run_id=$1", run_id)
        return dict(row) if row else {}
    finally:
        await conn.close()


async def _audit_rows(admin_dsn: str, run_id: str) -> list[dict]:
    conn = await asyncpg.connect(admin_dsn)
    try:
        rows = await conn.fetch(
            "SELECT action, resource_type, status, metadata FROM audit_events "
            "WHERE resource_id=$1 ORDER BY id",
            run_id,
        )
        return [dict(row) for row in rows]
    finally:
        await conn.close()


def _missing_in_airflow_invoke():
    calls: list[tuple[str, dict]] = []

    async def invoke(server, tool, args, *, user=None):
        calls.append((tool, dict(args)))
        if tool == "airflow_describe_dag":
            return {
                "found": True,
                "is_paused": True,
                "schedule_kind": "manual",
                "scheduler_healthy": True,
                "runs": [],
                "foreign": {"queued": 0, "running": 0, "stale_queued": 0},
            }
        if tool == "airflow_get_run_status":
            return {"found": False, "state": "not_found"}
        raise AssertionError(f"unexpected Airflow call {tool}")

    invoke.calls = calls
    return invoke


def _user(tenant: str, workspace: str) -> dict:
    return {
        "id": 42,
        "email": "ops@example.test",
        "role": "workspace_admin",
        "active_tenant_id": tenant,
        "active_workspace_id": workspace,
    }


async def _recover(console_dsn: str, user: dict, invoke, **kwargs):
    service = _service()
    pool = await asyncpg.create_pool(console_dsn, min_size=1, max_size=2)

    async def get_pool():
        return pool

    async def refresh(row, _user=None):
        raise AssertionError("no terminal sync expected in this scenario")

    try:
        return await service.recover_stuck_runs(
            user,
            actor="user:42",
            invoke=invoke,
            get_db_pool=get_pool,
            refresh_dag_run_status=refresh,
            **kwargs,
        )
    finally:
        await pool.close()


def test_recovery_closes_only_the_callers_run_under_rls_with_fence_and_audit(
    postgres_with_real_init_schema: str, omega_console_live_dsn: str
) -> None:
    async def scenario() -> None:
        admin = postgres_with_real_init_schema
        tenant_a, workspace_a = await _workspace(admin)
        tenant_b, workspace_b = await _workspace(admin)
        stuck_a = await _insert_run(admin, tenant=tenant_a, workspace=workspace_a)
        done_a = await _insert_run(admin, tenant=tenant_a, workspace=workspace_a, status="success")
        stuck_b = await _insert_run(admin, tenant=tenant_b, workspace=workspace_b)
        user_a = _user(tenant_a, workspace_a)

        dry = await _recover(omega_console_live_dsn, user_a, _missing_in_airflow_invoke(), mode="dry_run")
        assert dry.counts["candidates"] == 1
        assert [run["run_id"] for run in dry.runs] == [stuck_a]
        assert dry.runs[0]["classification"] == "missing_in_airflow"
        assert (await _row(admin, stuck_a))["status"] == "queued"

        applied = await _recover(
            omega_console_live_dsn,
            user_a,
            _missing_in_airflow_invoke(),
            mode="apply",
            expected_plan_digest=dry.plan_digest,
        )
        assert applied.counts["recovered"] == 1

        row = await _row(admin, stuck_a)
        assert row["status"] == "failed"
        assert row["error_message"] == "Recuperado por el sistema: tiempo de espera agotado"
        assert row["finished_at"] is not None
        assert row["lease_expires_at"] is None
        assert row["fencing_token"] == 1
        extra = row["extra"] if isinstance(row["extra"], dict) else json.loads(row["extra"])
        assert extra["reserved"] is True
        assert len(extra["reconciliation"]) == 1
        assert extra["reconciliation"][0]["reason"] == "missing_in_airflow"
        assert extra["recovery"]["previous_error"] == "trigger accepted"
        assert extra["recovery"]["actor"] == "user:42"

        audit = await _audit_rows(admin, stuck_a)
        assert [event["action"] for event in audit] == ["pipeline_run.recover_stuck"]
        metadata = audit[0]["metadata"]
        metadata = metadata if isinstance(metadata, dict) else json.loads(metadata)
        assert metadata["severity"] == "critical"
        assert metadata["workspace_id"] == workspace_a

        assert (await _row(admin, done_a))["status"] == "success"
        assert (await _row(admin, stuck_b))["status"] == "queued"
        assert await _audit_rows(admin, stuck_b) == []

        again = await _recover(omega_console_live_dsn, user_a, _missing_in_airflow_invoke(), mode="apply")
        assert again.counts["candidates"] == 0
        assert (await _row(admin, stuck_a))["fencing_token"] == 1

    asyncio.run(scenario())


def test_cas_refuses_changed_rows_and_foreign_workspaces(
    postgres_with_real_init_schema: str, omega_console_live_dsn: str
) -> None:
    async def scenario() -> None:
        admin = postgres_with_real_init_schema
        tenant_a, workspace_a = await _workspace(admin)
        tenant_b, workspace_b = await _workspace(admin)
        run_a = await _insert_run(admin, tenant=tenant_a, workspace=workspace_a)
        before = await _row(admin, run_a)
        recovery = importlib.import_module("app.domains.pipeline.stuck_run_recovery")
        verdict = recovery.RunVerdict("missing_in_airflow", "mark_failed", False, True, "x")

        pool = await asyncpg.create_pool(omega_console_live_dsn, min_size=1, max_size=2)

        async def get_pool():
            return pool

        async def record_event(**_kwargs):
            raise AssertionError("a lost CAS must not audit")

        service = _service()
        try:
            foreign_scope = {**before, "tenant_id": tenant_b, "workspace_id": workspace_b}
            assert await service._apply_mark_failed(
                foreign_scope, verdict, None, get_db_pool=get_pool,
                record_event=record_event, user=_user(tenant_b, workspace_b),
                actor="user:42", neutralized=False, digest="0" * 64,
            ) is None

            stale_fence = {**before, "fencing_token": 7}
            assert await service._apply_mark_failed(
                stale_fence, verdict, None, get_db_pool=get_pool,
                record_event=record_event, user=_user(tenant_a, workspace_a),
                actor="user:42", neutralized=False, digest="0" * 64,
            ) is None

            conn = await asyncpg.connect(admin)
            try:
                await conn.execute("UPDATE pipeline_runs SET status='running' WHERE run_id=$1", run_a)
            finally:
                await conn.close()
            assert await service._apply_mark_failed(
                before, verdict, None, get_db_pool=get_pool,
                record_event=record_event, user=_user(tenant_a, workspace_a),
                actor="user:42", neutralized=False, digest="0" * 64,
            ) is None
        finally:
            await pool.close()

        after = await _row(admin, run_a)
        assert after["status"] == "running"
        assert after["fencing_token"] == before["fencing_token"]
        assert await _audit_rows(admin, run_a) == []

        user_b = _user(tenant_b, workspace_b)
        report = await _recover(omega_console_live_dsn, user_b, _missing_in_airflow_invoke(), mode="dry_run")
        assert run_a not in {run["run_id"] for run in report.runs}

    asyncio.run(scenario())


def test_stale_plan_digest_changes_nothing(
    postgres_with_real_init_schema: str, omega_console_live_dsn: str
) -> None:
    async def scenario() -> None:
        admin = postgres_with_real_init_schema
        tenant, workspace = await _workspace(admin)
        run_id = await _insert_run(admin, tenant=tenant, workspace=workspace)
        user = _user(tenant, workspace)
        dry = await _recover(omega_console_live_dsn, user, _missing_in_airflow_invoke(), mode="dry_run")

        conn = await asyncpg.connect(admin)
        try:
            await conn.execute(
                "UPDATE pipeline_runs SET fencing_token = fencing_token + 1 WHERE run_id=$1",
                run_id,
            )
            rows_before = await conn.fetchval("SELECT count(*) FROM pipeline_runs")
        finally:
            await conn.close()

        with pytest.raises(_service().PlanChanged):
            await _recover(
                omega_console_live_dsn,
                user,
                _missing_in_airflow_invoke(),
                mode="apply",
                expected_plan_digest=dry.plan_digest,
            )
        row = await _row(admin, run_id)
        assert row["status"] == "queued"
        assert await _audit_rows(admin, run_id) == []
        conn = await asyncpg.connect(admin)
        try:
            assert await conn.fetchval("SELECT count(*) FROM pipeline_runs") == rows_before
        finally:
            await conn.close()

    asyncio.run(scenario())


def test_visibility_filter_and_orphan_scan_run_against_the_real_schema(
    postgres_with_real_init_schema: str, omega_console_live_dsn: str
) -> None:
    async def scenario() -> None:
        admin = postgres_with_real_init_schema
        tenant, workspace = await _workspace(admin)
        other_tenant, other_workspace = await _workspace(admin)
        sf_run = await _insert_run(admin, tenant=tenant, workspace=workspace)
        replicon_run = await _insert_run(
            admin, tenant=tenant, workspace=workspace, cartridge="replicon", dag_id="replicon_extract"
        )
        closed = await _insert_run(
            admin,
            tenant=tenant,
            workspace=workspace,
            status="failed",
            dag_id="sap_successfactors_extract_all",
        )
        foreign_closed = await _insert_run(
            admin,
            tenant=other_tenant,
            workspace=other_workspace,
            status="failed",
            dag_id="sap_successfactors_extract_all",
        )
        calls: list[tuple[str, dict]] = []

        async def invoke(server, tool, args, *, user=None):
            calls.append((tool, dict(args)))
            if tool == "airflow_describe_dag":
                pending = [
                    {"dag_run_id": run_id, "state": "queued", "queued_at": "2026-01-01T00:00:00+00:00", "conf": {"tenant_id": tenant, "workspace_id": workspace}}
                    for run_id in (closed, foreign_closed)
                ]
                return {"found": True, "is_paused": True, "schedule_kind": "manual", "scheduler_healthy": True, "runs": pending, "running_truncated": False, "foreign": {"queued": 0, "running": 0, "stale_queued": 0, "stale_running": 0}}
            if tool == "airflow_get_run_status":
                return {"found": False, "state": "not_found"}
            if tool == "airflow_mark_dag_run_failed":
                return {"marked": True, "found": True, "state": "failed"}
            raise AssertionError(tool)

        audits: list[dict] = []

        async def record_event(**payload):
            audits.append(payload)

        user = _user(tenant, workspace)
        report = await _recover(
            omega_console_live_dsn,
            user,
            invoke,
            mode="dry_run",
            visible_cartridges=["sap_successfactors"],
            orphan_scan=True,
            record_event=record_event,
        )
        planned = {run["run_id"]: run["classification"] for run in report.runs}
        assert planned == {sf_run: "missing_in_airflow", closed: "airflow_orphan"}
        assert replicon_run not in planned
        assert foreign_closed not in planned

        applied = await _recover(
            omega_console_live_dsn,
            user,
            invoke,
            mode="apply",
            expected_plan_digest=report.plan_digest,
            visible_cartridges=["sap_successfactors"],
            orphan_scan=True,
            record_event=record_event,
        )
        assert applied.counts["recovered"] == 1
        assert applied.counts["airflow_neutralized"] == 1
        assert (await _row(admin, closed))["status"] == "failed"
        assert (await _row(admin, closed))["fencing_token"] == 0
        assert (await _row(admin, replicon_run))["status"] == "queued"
        marked = [args["dag_run_id"] for tool, args in calls if tool == "airflow_mark_dag_run_failed"]
        assert marked == [closed]
        assert [event["metadata"]["classification"] for event in audits] == ["missing_in_airflow", "airflow_orphan"]

    asyncio.run(scenario())
