from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException


async def list_pipeline_runs(
    *,
    cartridge: str,
    entity: str | None,
    limit: int,
    user: dict[str, Any],
    get_db_pool: Any,
    pipeline_runs_scope_predicate: Any,
    pipeline_runs_read_conn: Any,
    sanitize_pipeline_run_for_user: Callable[[dict[str, Any], dict[str, Any]], dict],
    refresh_dag_run_status: Any,
    error_id_factory: Callable[[], str],
    logger_exception: Any,
) -> dict[str, Any]:
    try:
        pool = await get_db_pool()
        if entity:
            scope_sql, scope_values = await pipeline_runs_scope_predicate(user, 4)
            async with pipeline_runs_read_conn(pool, user) as conn:
                rows = await conn.fetch(
                    "SELECT * FROM pipeline_runs WHERE cartridge_id=$1 AND entity=$2 "
                    f"{scope_sql} ORDER BY started_at DESC NULLS LAST LIMIT $3",
                    cartridge,
                    entity,
                    limit,
                    *scope_values,
                )
        else:
            scope_sql, scope_values = await pipeline_runs_scope_predicate(user, 3)
            async with pipeline_runs_read_conn(pool, user) as conn:
                rows = await conn.fetch(
                    "SELECT * FROM pipeline_runs WHERE cartridge_id=$1 "
                    f"{scope_sql} ORDER BY started_at DESC NULLS LAST LIMIT $2",
                    cartridge,
                    limit,
                    *scope_values,
                )
        response_rows = [sanitize_pipeline_run_for_user(dict(row), user) for row in rows]
        if entity:
            refreshed_rows = []
            for row in response_rows:
                refreshed_rows.append(
                    sanitize_pipeline_run_for_user(
                        await refresh_dag_run_status(row, user),
                        user,
                    )
                )
            response_rows = refreshed_rows
        return {"runs": response_rows}
    except Exception:
        error_id = error_id_factory()
        logger_exception("pipeline runs query failed error_id=%s", error_id)
        raise HTTPException(500, f"Internal server error. error_id={error_id}")


async def list_pipeline_entity_runs(
    *,
    cartridge: str,
    entity: str,
    limit: int,
    user: dict[str, Any],
    get_db_pool: Any,
    pipeline_runs_scope_predicate: Any,
    pipeline_runs_read_conn: Any,
    refresh_dag_run_status: Any,
    format_pipeline_entity_run: Callable[[dict[str, Any], dict[str, Any]], dict],
    error_id_factory: Callable[[], str],
    logger_exception: Any,
) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 20), 100))

    try:
        pool = await get_db_pool()
        scope_sql, scope_values = await pipeline_runs_scope_predicate(user, 4)
        async with pipeline_runs_read_conn(pool, user) as conn:
            rows = await conn.fetch(
                f"""
                SELECT run_id, dag_id, airflow_dag_run_id, status, mode,
                       started_at, finished_at, duration_seconds,
                       record_count, bytes_written, storage_uri, watermark_updated_to,
                       error_message, extra
                  FROM pipeline_runs
                 WHERE cartridge_id=$1 AND entity=$2
                   {scope_sql}
                 ORDER BY started_at DESC NULLS LAST
                 LIMIT $3
                """,
                cartridge,
                entity,
                safe_limit,
                *scope_values,
            )
    except Exception:
        error_id = error_id_factory()
        logger_exception("entity runs query failed error_id=%s", error_id)
        raise HTTPException(500, f"Internal server error. error_id={error_id}")

    runs = []
    for row in rows:
        refreshed = await refresh_dag_run_status(dict(row), user)
        runs.append(format_pipeline_entity_run(refreshed, user))

    return {
        "cartridge": cartridge,
        "entity": entity,
        "runs": runs,
    }


async def pipeline_run_logs_payload(
    *,
    cartridge: str,
    entity: str,
    dag_run_id: str,
    metadata: dict[str, Any],
    user: dict[str, Any],
    get_db_pool: Any,
    pipeline_runs_scope_predicate: Any,
    pipeline_runs_read_conn: Any,
    build_pipeline_run_logs_payload: Any,
    refresh_dag_run_status: Any,
    mcp_invoke: Any,
    normalize_airflow_state: Any,
    airflow_log_task_ids: Any,
    airflow_log_attempt: Any,
    error_id_factory: Callable[[], str],
    logger_exception: Any,
) -> dict[str, Any]:
    try:
        pool = await get_db_pool()
        scope_sql, scope_values = await pipeline_runs_scope_predicate(user, 4)
        async with pipeline_runs_read_conn(pool, user) as conn:
            row = await conn.fetchrow(
                f"""
                SELECT run_id, dag_id, airflow_dag_run_id, status, mode,
                       started_at, finished_at, duration_seconds, error_message
                  FROM pipeline_runs
                 WHERE cartridge_id=$1
                   AND entity=$2
                   AND (run_id=$3 OR airflow_dag_run_id=$3)
                   {scope_sql}
                 ORDER BY started_at DESC NULLS LAST
                 LIMIT 1
                """,
                cartridge,
                entity,
                dag_run_id,
                *scope_values,
            )
    except Exception:
        error_id = error_id_factory()
        logger_exception("run logs query failed error_id=%s", error_id)
        raise HTTPException(500, f"Internal server error. error_id={error_id}")

    if not row:
        raise HTTPException(
            404,
            {
                "error": f"Run '{dag_run_id}' not found for {cartridge}/{entity}",
                "attempted": {
                    "dag_id": metadata.get("dag_id"),
                    "dag_run_id": dag_run_id,
                    "task_ids": [],
                },
            },
        )

    return await build_pipeline_run_logs_payload(
        cartridge=cartridge,
        entity=entity,
        dag_run_id=dag_run_id,
        run_row=dict(row),
        metadata=metadata,
        user=user,
        refresh_dag_run_status=refresh_dag_run_status,
        mcp_invoke=mcp_invoke,
        normalize_airflow_state=normalize_airflow_state,
        airflow_log_task_ids=airflow_log_task_ids,
        airflow_log_attempt=airflow_log_attempt,
        error_id_factory=error_id_factory,
        logger_exception=logger_exception,
    )
