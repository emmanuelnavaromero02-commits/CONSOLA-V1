"""SuccessFactors extraction reservation helpers."""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from app.domains.pipeline.status_transitions import (
    postgres_monotonic_status,
    postgres_status_accepts,
)


def reservation_applies(
    *, cartridge: str, dag_id: str, expected_cartridge: str, entity_dag_id: str
) -> bool:
    return cartridge == expected_cartridge and dag_id == entity_dag_id


def reservation_run_id(
    *,
    dag_id: str,
    requested_dag_run_id: str | None,
    entity_fragment: str,
    token: str,
) -> str:
    return requested_dag_run_id or f"console__{dag_id}__{entity_fragment}__{token}"


def reservation_extra(conf: dict[str, Any]) -> str:
    return json.dumps(
        {
            "raw_conf": conf,
            "triggered_by": "console",
            "reserved": True,
            "reason": "successfactors_entity_extract_backpressure",
        }
    )


def active_scope_args(
    *,
    cartridge: str,
    active_window_seconds: int,
    scope_columns_present: bool,
    tenant_id: Any | None,
    workspace_id: Any | None,
) -> tuple[str, list[Any]]:
    scope_sql = ""
    args: list[Any] = [cartridge, active_window_seconds]
    if scope_columns_present:
        scope_sql = "AND tenant_id=$3::uuid AND workspace_id=$4::uuid"
        args.extend([tenant_id, workspace_id])
    return scope_sql, args


def extract_all_conflict(
    rows: list[dict[str, Any]], *, extract_all_dag_id: str, aggregate_entity: str
) -> dict[str, Any] | None:
    for row in rows:
        if row.get("dag_id") == extract_all_dag_id and row.get("entity") == aggregate_entity:
            return {
                "reason": "extract_all_already_running",
                "message": (
                    "SAP SuccessFactors extract_all is already running; "
                    "wait for it to finish before triggering individual entities."
                ),
                "job_id": row.get("airflow_dag_run_id") or row.get("run_id"),
            }
    return None


def active_entity_run(
    rows: list[dict[str, Any]], *, entity: str, entity_dag_id: str
) -> dict[str, Any] | None:
    for row in rows:
        if row.get("dag_id") == entity_dag_id and row.get("entity") == entity:
            return row
    return None


def active_entity_run_count(rows: list[dict[str, Any]], *, entity_dag_id: str) -> int:
    return sum(1 for row in rows if row.get("dag_id") == entity_dag_id)


def active_entity_limit_payload(*, active: int, limit: int) -> dict[str, Any]:
    return {
        "reason": "too_many_active_entity_extracts",
        "message": (
            "SAP SuccessFactors extraction backpressure: "
            f"{active} active entity runs; use Extract All/sync or wait."
        ),
        "active": active,
        "limit": limit,
    }


async def reserve_entity_extract_slot(
    *,
    cartridge: str,
    entity: str,
    dag_id: str,
    conf: dict[str, Any],
    user: dict[str, Any] | None,
    requested_dag_run_id: str | None,
    expected_cartridge: str,
    entity_dag_id: str,
    extract_all_dag_id: str,
    aggregate_entity: str,
    active_window_seconds: int,
    max_active_entity_extracts: int,
    build_security_context: Any,
    table_has_column: Any,
    airflow_run_id_fragment: Any,
    active_extract_run_payload: Any,
    get_db_pool: Any,
    token: str,
    logger_exception: Any | None = None,
) -> dict[str, Any] | None:
    if not reservation_applies(
        cartridge=cartridge,
        dag_id=dag_id,
        expected_cartridge=expected_cartridge,
        entity_dag_id=entity_dag_id,
    ):
        return None

    security_context = build_security_context(user)
    tenant_id = conf.get("tenant_id") or security_context.get("tenant_id")
    workspace_id = conf.get("workspace_id") or security_context.get("workspace_id")
    scope_columns_present = await table_has_column(
        "pipeline_runs", "tenant_id", refresh=True
    ) and await table_has_column("pipeline_runs", "workspace_id", refresh=True)
    if scope_columns_present and not (tenant_id and workspace_id):
        raise HTTPException(403, "pipeline run tenant/workspace scope is required")

    dag_run_id = reservation_run_id(
        dag_id=dag_id,
        requested_dag_run_id=requested_dag_run_id,
        entity_fragment=airflow_run_id_fragment(entity),
        token=token,
    )
    extra = reservation_extra(conf)
    monotonic_status = postgres_monotonic_status(
        "pipeline_runs.status", "EXCLUDED.status"
    )
    accepts_status = postgres_status_accepts(
        "pipeline_runs.status", "EXCLUDED.status"
    )
    pool = await get_db_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                if scope_columns_present:
                    await conn.execute(
                        "SELECT set_config('app.tenant_id', $1, true), "
                        "set_config('app.workspace_id', $2, true)",
                        tenant_id,
                        workspace_id,
                    )
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
                    expected_cartridge,
                    f"{tenant_id or '*'}:{workspace_id or '*'}",
                )
                scope_sql, args = active_scope_args(
                    cartridge=expected_cartridge,
                    active_window_seconds=active_window_seconds,
                    scope_columns_present=scope_columns_present,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                )
                rows = [
                    dict(row)
                    for row in await conn.fetch(
                        f"""
                        SELECT run_id, dag_id, entity, airflow_dag_run_id,
                               status, mode, started_at, extra
                          FROM pipeline_runs
                         WHERE cartridge_id=$1
                           AND status IN ('queued', 'running')
                           AND started_at > NOW() - ($2::integer * INTERVAL '1 second')
                           {scope_sql}
                         ORDER BY started_at DESC
                         LIMIT 50
                        """,
                        *args,
                    )
                ]

                extract_all_running = extract_all_conflict(
                    rows,
                    extract_all_dag_id=extract_all_dag_id,
                    aggregate_entity=aggregate_entity,
                )
                if extract_all_running:
                    raise HTTPException(429, detail=extract_all_running)

                active_row = active_entity_run(
                    rows,
                    entity=entity,
                    entity_dag_id=entity_dag_id,
                )
                if active_row:
                    return {
                        "response": active_extract_run_payload(
                            row=active_row,
                            cartridge=cartridge,
                            entity=entity,
                            dag_id=dag_id,
                            conf=conf,
                            reason="active_entity_run",
                        )
                    }

                active_entity_runs = active_entity_run_count(
                    rows,
                    entity_dag_id=entity_dag_id,
                )
                if active_entity_runs >= max_active_entity_extracts:
                    raise HTTPException(
                        429,
                        detail=active_entity_limit_payload(
                            active=active_entity_runs,
                            limit=max_active_entity_extracts,
                        ),
                    )

                if scope_columns_present:
                    await conn.execute(
                        f"""
                        INSERT INTO pipeline_runs (
                            run_id, dag_id, cartridge_id, entity, airflow_dag_run_id,
                            mode, status, started_at, extra, tenant_id, workspace_id
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, 'queued', NOW(), $7::jsonb, $8::uuid, $9::uuid)
                        ON CONFLICT (run_id) DO UPDATE SET
                            airflow_dag_run_id = CASE WHEN {accepts_status}
                                THEN EXCLUDED.airflow_dag_run_id
                                ELSE pipeline_runs.airflow_dag_run_id END,
                            mode = CASE WHEN {accepts_status}
                                THEN EXCLUDED.mode ELSE pipeline_runs.mode END,
                            status = {monotonic_status},
                            tenant_id = COALESCE(pipeline_runs.tenant_id, EXCLUDED.tenant_id),
                            workspace_id = COALESCE(pipeline_runs.workspace_id, EXCLUDED.workspace_id),
                            extra = CASE WHEN {accepts_status}
                                THEN COALESCE(pipeline_runs.extra, '{{}}'::jsonb) || EXCLUDED.extra
                                ELSE pipeline_runs.extra END
                        """,
                        dag_run_id,
                        dag_id,
                        cartridge,
                        entity,
                        dag_run_id,
                        conf.get("mode") or "incremental",
                        extra,
                        tenant_id,
                        workspace_id,
                    )
                else:
                    await conn.execute(
                        f"""
                        INSERT INTO pipeline_runs (
                            run_id, dag_id, cartridge_id, entity, airflow_dag_run_id,
                            mode, status, started_at, extra
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, 'queued', NOW(), $7::jsonb)
                        ON CONFLICT (run_id) DO UPDATE SET
                            airflow_dag_run_id = CASE WHEN {accepts_status}
                                THEN EXCLUDED.airflow_dag_run_id
                                ELSE pipeline_runs.airflow_dag_run_id END,
                            mode = CASE WHEN {accepts_status}
                                THEN EXCLUDED.mode ELSE pipeline_runs.mode END,
                            status = {monotonic_status},
                            extra = CASE WHEN {accepts_status}
                                THEN COALESCE(pipeline_runs.extra, '{{}}'::jsonb) || EXCLUDED.extra
                                ELSE pipeline_runs.extra END
                        """,
                        dag_run_id,
                        dag_id,
                        cartridge,
                        entity,
                        dag_run_id,
                        conf.get("mode") or "incremental",
                        extra,
                    )
    except HTTPException:
        raise
    except Exception as exc:
        if logger_exception is not None:
            logger_exception("SuccessFactors extraction backpressure check failed")
        raise HTTPException(
            503,
            detail={
                "reason": "backpressure_unavailable",
                "message": "Could not reserve SAP SuccessFactors extraction slot.",
                "error": str(exc),
            },
        ) from exc

    return {"dag_run_id": dag_run_id, "reserved": True}
