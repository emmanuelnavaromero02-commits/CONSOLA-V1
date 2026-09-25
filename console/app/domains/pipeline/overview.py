from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


async def build_pipeline_overview(
    *,
    cartridge: str,
    user: dict | None,
    get_cartridge: Callable[[str], Awaitable[dict[str, Any] | None]],
    mcp_invoke: Callable[..., Awaitable[Any]],
    get_db_pool: Callable[[], Awaitable[Any]],
    pipeline_runs_scope_predicate: Callable[..., Awaitable[tuple[str, list[Any]]]],
    pipeline_runs_read_conn: Callable[..., Any],
    pipeline_gather_by_entity: Callable[..., Awaitable[tuple[dict, set, set]]],
    refresh_dag_run_status: Callable[[dict[str, Any], dict | None], Awaitable[dict]],
    call_with_optional_user: Callable[..., Awaitable[Any]],
    job_list_recent: Callable[..., Awaitable[list[dict]]],
    refinement_invoke: Callable[..., Awaitable[dict[str, Any]]],
    bronze_physical_snapshot: Callable[..., Awaitable[dict[str, Any]]],
    pipeline_jobs_by_entity: Callable[[list[dict]], dict[str, dict]],
    pipeline_bronze_date_count: Callable[[dict | None, dict | None], tuple[Any, Any]],
    pipeline_silver_datasets_by_source: Callable[[list[dict]], dict[str, list[dict]]],
    pipeline_entity_row: Callable[..., dict[str, Any]],
    pipeline_response_payload: Callable[[list[dict], dict[str, set[str]]], dict],
    http_client_factory: Any,
    headers_factory: Callable[[str], dict[str, str]],
    refinement_url: str,
    dag_status_timeout_sec: float,
    datasets_timeout_sec: float,
    bronze_snapshot_timeout_sec: float,
    logger_debug: Callable[..., None] | None = None,
) -> dict[str, Any]:
    entity_list = await _pipeline_entity_list(
        cartridge=cartridge,
        user=user,
        get_cartridge=get_cartridge,
        mcp_invoke=mcp_invoke,
    )

    partial_reasons: dict[str, set[str]] = {}

    def mark_partial(entity: str, reason: str) -> None:
        if entity:
            partial_reasons.setdefault(entity, set()).add(reason)

    dag_runs_by_entity = await _pipeline_dag_runs_by_entity(
        cartridge=cartridge,
        user=user,
        get_db_pool=get_db_pool,
        pipeline_runs_scope_predicate=pipeline_runs_scope_predicate,
        pipeline_runs_read_conn=pipeline_runs_read_conn,
        pipeline_gather_by_entity=pipeline_gather_by_entity,
        refresh_dag_run_status=refresh_dag_run_status,
        dag_status_timeout_sec=dag_status_timeout_sec,
        mark_partial=mark_partial,
        logger_debug=logger_debug,
    )

    all_jobs = await call_with_optional_user(job_list_recent, 100, user=user)
    jobs_by_entity = pipeline_jobs_by_entity(all_jobs)

    all_datasets = await _pipeline_refinement_datasets(
        refinement_invoke=refinement_invoke,
        datasets_timeout_sec=datasets_timeout_sec,
        user=user,
        http_client_factory=http_client_factory,
        headers_factory=headers_factory,
        refinement_url=refinement_url,
    )
    silver_ds = [d for d in all_datasets if d.get("layer") == "silver"]
    gold_ds = [d for d in all_datasets if d.get("layer") == "gold"]
    silver_by_source = pipeline_silver_datasets_by_source(silver_ds)

    physical_bronze_by_entity = await _pipeline_physical_bronze_by_entity(
        cartridge=cartridge,
        user=user,
        entity_list=entity_list,
        dag_runs_by_entity=dag_runs_by_entity,
        jobs_by_entity=jobs_by_entity,
        call_with_optional_user=call_with_optional_user,
        bronze_physical_snapshot=bronze_physical_snapshot,
        pipeline_bronze_date_count=pipeline_bronze_date_count,
        pipeline_gather_by_entity=pipeline_gather_by_entity,
        bronze_snapshot_timeout_sec=bronze_snapshot_timeout_sec,
        mark_partial=mark_partial,
    )

    rows = []
    for entity_config in entity_list:
        entity = entity_config.get("entity") or entity_config.get("name") or ""
        rows.append(
            pipeline_entity_row(
                entity_config=entity_config,
                cartridge=cartridge,
                dag_run=dag_runs_by_entity.get(entity),
                last_job=jobs_by_entity.get(entity),
                physical_bronze=physical_bronze_by_entity.get(entity),
                partial_reasons=partial_reasons.get(entity),
                silver_by_source=silver_by_source,
                gold_datasets=gold_ds,
            )
        )

    return pipeline_response_payload(rows, partial_reasons)


async def _pipeline_entity_list(
    *,
    cartridge: str,
    user: dict | None,
    get_cartridge: Callable[[str], Awaitable[dict[str, Any] | None]],
    mcp_invoke: Callable[..., Awaitable[Any]],
) -> list[dict[str, Any]]:
    entity_list: list[dict[str, Any]] = []
    manifest = await get_cartridge(cartridge)
    if manifest:
        for entity_config in manifest.get("entities") or []:
            entity_list.append(
                {
                    "entity": entity_config.get("id")
                    or entity_config.get("entity")
                    or "",
                    "mode": entity_config.get("mode", "full"),
                    "watermark_field": entity_config.get("watermark_field"),
                    "description": entity_config.get("description", ""),
                }
            )
    if entity_list:
        return entity_list

    entities_raw = await mcp_invoke(cartridge, "list_entities", {}, user=user)
    if isinstance(entities_raw, dict):
        return entities_raw.get("entities", entities_raw.get("result", []))
    if isinstance(entities_raw, list):
        return entities_raw
    return []


async def _pipeline_dag_runs_by_entity(
    *,
    cartridge: str,
    user: dict | None,
    get_db_pool: Callable[[], Awaitable[Any]],
    pipeline_runs_scope_predicate: Callable[..., Awaitable[tuple[str, list[Any]]]],
    pipeline_runs_read_conn: Callable[..., Any],
    pipeline_gather_by_entity: Callable[..., Awaitable[tuple[dict, set, set]]],
    refresh_dag_run_status: Callable[[dict[str, Any], dict | None], Awaitable[dict]],
    dag_status_timeout_sec: float,
    mark_partial: Callable[[str, str], None],
    logger_debug: Callable[..., None] | None,
) -> dict[str, dict]:
    dag_runs_by_entity: dict[str, dict] = {}
    try:
        pool = await get_db_pool()
        scope_sql, scope_values = await pipeline_runs_scope_predicate(user, 2)
        async with pipeline_runs_read_conn(pool, user) as conn:
            rows_pg = await conn.fetch(
                f"""SELECT DISTINCT ON (entity)
                       run_id, dag_id, entity, airflow_dag_run_id,
                       status, mode,
                       started_at, finished_at,
                       record_count, bytes_written, storage_uri,
                       duration_seconds, watermark_updated_to, error_message, extra
                   FROM pipeline_runs
                   WHERE cartridge_id = $1
                     {scope_sql}
                   ORDER BY entity, started_at DESC""",
                cartridge,
                *scope_values,
            )
        raw_runs_by_entity = {row["entity"]: dict(row) for row in rows_pg}
        refreshed, pending, failed = await pipeline_gather_by_entity(
            {
                entity: refresh_dag_run_status(dict(row), user)
                for entity, row in raw_runs_by_entity.items()
            },
            dag_status_timeout_sec,
        )
        for entity, row in raw_runs_by_entity.items():
            dag_runs_by_entity[entity] = refreshed.get(entity) or row
        for entity in pending | failed:
            mark_partial(entity, "airflow_status_refresh")
    except Exception:
        if logger_debug is not None:
            logger_debug(
                "Could not load pipeline_runs for %s", cartridge, exc_info=True
            )
    return dag_runs_by_entity


async def _pipeline_refinement_datasets(
    *,
    refinement_invoke: Callable[..., Awaitable[dict[str, Any]]],
    datasets_timeout_sec: float,
    user: dict | None,
    http_client_factory: Any,
    headers_factory: Callable[[str], dict[str, str]],
    refinement_url: str,
) -> list[dict[str, Any]]:
    try:
        return (
            await refinement_invoke(
                "list_datasets", {}, timeout=datasets_timeout_sec, user=user
            )
        ).get("datasets", [])
    except Exception:
        if hasattr(http_client_factory, "post"):
            return []
        try:
            async with http_client_factory(
                headers=headers_factory("REFINEMENT"), timeout=15
            ) as client:
                response = await client.get(f"{refinement_url}/datasets")
            if getattr(response, "status_code", 500) == 200:
                return (response.json() or {}).get("datasets", [])
        except Exception:
            return []
    return []


async def _pipeline_physical_bronze_by_entity(
    *,
    cartridge: str,
    user: dict | None,
    entity_list: list[dict[str, Any]],
    dag_runs_by_entity: dict[str, dict],
    jobs_by_entity: dict[str, dict],
    call_with_optional_user: Callable[..., Awaitable[Any]],
    bronze_physical_snapshot: Callable[..., Awaitable[dict[str, Any]]],
    pipeline_bronze_date_count: Callable[[dict | None, dict | None], tuple[Any, Any]],
    pipeline_gather_by_entity: Callable[..., Awaitable[tuple[dict, set, set]]],
    bronze_snapshot_timeout_sec: float,
    mark_partial: Callable[[str, str], None],
) -> dict[str, dict]:
    snapshot_work: dict[str, Any] = {}
    for entity_config in entity_list:
        entity = entity_config.get("entity") or entity_config.get("name") or ""
        if not entity:
            continue
        dag_run = dag_runs_by_entity.get(entity)
        last_job = jobs_by_entity.get(entity)
        bronze_date, bronze_count = pipeline_bronze_date_count(dag_run, last_job)
        if not bronze_date or bronze_count is None:
            snapshot_work[entity] = call_with_optional_user(
                bronze_physical_snapshot,
                cartridge,
                entity,
                user=user,
            )

    physical_bronze_by_entity, pending, failed = await pipeline_gather_by_entity(
        snapshot_work,
        bronze_snapshot_timeout_sec,
    )
    for entity in pending | failed:
        mark_partial(entity, "bronze_snapshot")
    return physical_bronze_by_entity
