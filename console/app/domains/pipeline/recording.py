"""Persistence helpers for pipeline run triggers."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from fastapi import HTTPException


GetDbPool = Callable[[], Awaitable[object]]
TableHasColumn = Callable[..., Awaitable[bool]]
NormalizeState = Callable[[str | None], str]


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
