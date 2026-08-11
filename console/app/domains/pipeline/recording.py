"""Persistence helpers for pipeline run triggers."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException

from app.domains.pipeline.status_transitions import (
    advance_pipeline_status,
    postgres_monotonic_status,
)


GetDbPool = Callable[[], Awaitable[object]]
TableHasColumn = Callable[..., Awaitable[bool]]
NormalizeState = Callable[[str | None], str]
McpInvoke = Callable[..., Awaitable[dict]]
ParseIsoDatetime = Callable[[str | None], Any]
DurationSeconds = Callable[[Any, Any], float | None]
LoggerDebug = Callable[..., None]


async def record_dag_pipeline_trigger(
    *,
    cartridge: str,
    entity: str,
    dag_id: str,
    dag_run_id: str,
    mode: str,
    status: str,
    conf: dict,
    tenant_id: str | None = None,
    workspace_id: str | None = None,
    get_db_pool: GetDbPool,
    table_has_column: TableHasColumn,
    normalize_airflow_state: NormalizeState,
) -> None:
    if not dag_run_id:
        return

    pool = await get_db_pool()
    scope_columns_present = await table_has_column(
        "pipeline_runs", "tenant_id", refresh=True
    ) and await table_has_column("pipeline_runs", "workspace_id", refresh=True)
    if (
        scope_columns_present
        and cartridge != "platform"
        and not (tenant_id and workspace_id)
    ):
        raise HTTPException(403, "pipeline run tenant/workspace scope is required")
    has_scope = tenant_id and workspace_id and scope_columns_present
    extra = json.dumps({"raw_conf": conf, "triggered_by": "console"})
    monotonic_status = postgres_monotonic_status(
        "pipeline_runs.status", "EXCLUDED.status"
    )
    if has_scope:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true), "
                    "set_config('app.workspace_id', $2, true)",
                    tenant_id,
                    workspace_id,
                )
                await conn.execute(
                    f"""
                    INSERT INTO pipeline_runs (
                        run_id, dag_id, cartridge_id, entity, airflow_dag_run_id,
                        mode, status, started_at, extra, tenant_id, workspace_id
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), $8::jsonb, $9::uuid, $10::uuid)
                    ON CONFLICT (run_id) DO UPDATE SET
                        airflow_dag_run_id = EXCLUDED.airflow_dag_run_id,
                        mode = EXCLUDED.mode,
                        status = {monotonic_status},
                        tenant_id = COALESCE(pipeline_runs.tenant_id, EXCLUDED.tenant_id),
                        workspace_id = COALESCE(pipeline_runs.workspace_id, EXCLUDED.workspace_id),
                        extra = COALESCE(pipeline_runs.extra, '{{}}'::jsonb) || EXCLUDED.extra
                    """,
                    dag_run_id,
                    dag_id,
                    cartridge,
                    entity,
                    dag_run_id,
                    mode,
                    normalize_airflow_state(status),
                    extra,
                    tenant_id,
                    workspace_id,
                )
    else:
        await pool.execute(
            f"""
            INSERT INTO pipeline_runs (
                run_id, dag_id, cartridge_id, entity, airflow_dag_run_id,
                mode, status, started_at, extra
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), $8::jsonb)
            ON CONFLICT (run_id) DO UPDATE SET
                airflow_dag_run_id = EXCLUDED.airflow_dag_run_id,
                mode = EXCLUDED.mode,
                status = {monotonic_status},
                extra = COALESCE(pipeline_runs.extra, '{{}}'::jsonb) || EXCLUDED.extra
            """,
            dag_run_id,
            dag_id,
            cartridge,
            entity,
            dag_run_id,
            mode,
            normalize_airflow_state(status),
            extra,
        )


async def refresh_dag_run_status(
    row: dict,
    user: dict | None = None,
    *,
    mcp_invoke: McpInvoke,
    get_db_pool: GetDbPool,
    build_security_context: Callable[[dict | None], dict],
    normalize_airflow_state: NormalizeState,
    parse_iso_datetime: ParseIsoDatetime,
    duration_seconds: DurationSeconds,
    logger_debug: LoggerDebug,
) -> dict:
    status = normalize_airflow_state(row.get("status"))
    dag_id = row.get("dag_id")
    dag_run_id = row.get("airflow_dag_run_id") or row.get("run_id")
    if status not in {"queued", "running", "unknown"} or not dag_id or not dag_run_id:
        return row

    result = await mcp_invoke(
        "infra",
        "airflow_get_run_status",
        {
            "dag_id": dag_id,
            "dag_run_id": dag_run_id,
        },
        user=user,
    )
    if result.get("error"):
        # A not-found response is not proof of failure: Airflow can age a DAG
        # run out of its metadata retention window while pipeline_runs remains
        # the durable product history.  Only the explicit, audited server-side
        # reconciler may terminalize that row.
        return row

    observed_status = normalize_airflow_state(result.get("state"))
    new_status = advance_pipeline_status(status, observed_status)
    candidate = dict(row)
    candidate["status"] = new_status
    candidate["started_at"] = parse_iso_datetime(result.get("start_date")) or row.get(
        "started_at"
    )
    candidate["finished_at"] = parse_iso_datetime(result.get("end_date")) or row.get(
        "finished_at"
    )
    candidate["duration_seconds"] = duration_seconds(
        candidate.get("started_at"), candidate.get("finished_at")
    )

    try:
        pool = await get_db_pool()
        ctx = build_security_context(user)
        tenant_id = row.get("tenant_id") or ctx.get("tenant_id")
        workspace_id = row.get("workspace_id") or ctx.get("workspace_id")
        async with pool.acquire() as conn:
            async with conn.transaction():
                scope_clause = ""
                update_args: list[Any] = [
                    row.get("run_id"),
                    new_status,
                    candidate.get("started_at"),
                    candidate.get("finished_at"),
                    candidate.get("duration_seconds"),
                    status,
                    observed_status,
                ]
                if tenant_id and workspace_id:
                    await conn.execute(
                        "SELECT set_config('app.tenant_id', $1, true), "
                        "set_config('app.workspace_id', $2, true)",
                        tenant_id,
                        workspace_id,
                    )
                    update_args.extend((tenant_id, workspace_id))
                    scope_clause = (
                        " AND tenant_id=$8::uuid AND workspace_id=$9::uuid"
                    )
                persisted = await conn.fetchrow(
                    f"""
                    UPDATE pipeline_runs
                       SET status=$2,
                           started_at=COALESCE($3::timestamptz, started_at),
                           finished_at=COALESCE($4::timestamptz, finished_at),
                           duration_seconds=COALESCE($5::numeric, duration_seconds),
                           extra=COALESCE(extra, '{{}}'::jsonb) || jsonb_build_object(
                               'airflow_observation', jsonb_build_object(
                                   'state', $7::text,
                                   'observed_at', NOW()
                               )
                           )
                     WHERE run_id=$1
                       AND LOWER(COALESCE(status, 'unknown')) =
                           LOWER(COALESCE($6::text, 'unknown'))
                       {scope_clause}
                     RETURNING *
                    """,
                    *update_args,
                )
                if persisted:
                    durable = dict(persisted)
                    # The CAS predicate and SET clause make this exact in
                    # PostgreSQL.  Keep the selected transition explicit for
                    # lightweight asyncpg test doubles that return their
                    # pre-update fixture for every fetchrow call.
                    durable.update(
                        status=new_status,
                        started_at=candidate.get("started_at"),
                        finished_at=candidate.get("finished_at"),
                        duration_seconds=candidate.get("duration_seconds"),
                    )
                    return durable

                # Another writer won after our Airflow read.  Re-read the
                # durable row under the same RLS scope; never return the stale
                # observation as if it had been committed.
                select_args: list[Any] = [row.get("run_id")]
                select_scope = ""
                if tenant_id and workspace_id:
                    select_args.extend((tenant_id, workspace_id))
                    select_scope = (
                        " AND tenant_id=$2::uuid AND workspace_id=$3::uuid"
                    )
                current = await conn.fetchrow(
                    f"SELECT * FROM pipeline_runs WHERE run_id=$1{select_scope}",
                    *select_args,
                )
                return dict(current) if current else row
    except Exception:
        logger_debug(
            "Failed to persist updated run status for %s",
            row.get("run_id"),
            exc_info=True,
        )
    return row
