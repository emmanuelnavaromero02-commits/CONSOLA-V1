"""Persistence helpers for pipeline run triggers."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException


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
                    """
                    INSERT INTO pipeline_runs (
                        run_id, dag_id, cartridge_id, entity, airflow_dag_run_id,
                        mode, status, started_at, extra, tenant_id, workspace_id
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), $8::jsonb, $9::uuid, $10::uuid)
                    ON CONFLICT (run_id) DO UPDATE SET
                        airflow_dag_run_id = EXCLUDED.airflow_dag_run_id,
                        mode = EXCLUDED.mode,
                        status = EXCLUDED.status,
                        tenant_id = COALESCE(pipeline_runs.tenant_id, EXCLUDED.tenant_id),
                        workspace_id = COALESCE(pipeline_runs.workspace_id, EXCLUDED.workspace_id),
                        extra = pipeline_runs.extra || EXCLUDED.extra
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
            """
            INSERT INTO pipeline_runs (
                run_id, dag_id, cartridge_id, entity, airflow_dag_run_id,
                mode, status, started_at, extra
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), $8::jsonb)
            ON CONFLICT (run_id) DO UPDATE SET
                airflow_dag_run_id = EXCLUDED.airflow_dag_run_id,
                mode = EXCLUDED.mode,
                status = EXCLUDED.status,
                extra = pipeline_runs.extra || EXCLUDED.extra
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
        return row

    new_status = normalize_airflow_state(result.get("state"))
    row["status"] = new_status
    row["started_at"] = parse_iso_datetime(result.get("start_date")) or row.get(
        "started_at"
    )
    row["finished_at"] = parse_iso_datetime(result.get("end_date")) or row.get(
        "finished_at"
    )
    row["duration_seconds"] = duration_seconds(
        row.get("started_at"), row.get("finished_at")
    )

    try:
        pool = await get_db_pool()
        ctx = build_security_context(user)
        tenant_id = row.get("tenant_id") or ctx.get("tenant_id")
        workspace_id = row.get("workspace_id") or ctx.get("workspace_id")
        if tenant_id and workspace_id:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "SELECT set_config('app.tenant_id', $1, true), "
                        "set_config('app.workspace_id', $2, true)",
                        tenant_id,
                        workspace_id,
                    )
                    await conn.execute(
                        """
                        UPDATE pipeline_runs
                           SET status=$2,
                               started_at=COALESCE($3::timestamptz, started_at),
                               finished_at=COALESCE($4::timestamptz, finished_at),
                               duration_seconds=COALESCE($5::numeric, duration_seconds)
                         WHERE run_id=$1
                        """,
                        row.get("run_id"),
                        new_status,
                        row.get("started_at"),
                        row.get("finished_at"),
                        row.get("duration_seconds"),
                    )
        else:
            await pool.execute(
                """
                UPDATE pipeline_runs
                   SET status=$2,
                       started_at=COALESCE($3::timestamptz, started_at),
                       finished_at=COALESCE($4::timestamptz, finished_at),
                       duration_seconds=COALESCE($5::numeric, duration_seconds)
                 WHERE run_id=$1
                """,
                row.get("run_id"),
                new_status,
                row.get("started_at"),
                row.get("finished_at"),
                row.get("duration_seconds"),
            )
    except Exception:
        logger_debug(
            "Failed to persist updated run status for %s",
            row.get("run_id"),
            exc_info=True,
        )
    return row
