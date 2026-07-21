from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

from app.domains.pipeline.run_state import normalize_airflow_state
from app.services import sync_progress

SYNC_NOW_ENTITY = "__sync_now__"
SYNC_AGGREGATE_ENTITY = "__extract_all__"
SYNC_NOW_DAG_ID = "sync_now"
SYNC_TERMINAL_STATUSES = {
    "success",
    "partial",
    "failed",
    "blocked",
    "skipped",
    "skipped_explicit",
}
SYNC_STEP_RECOMPUTE_PERCENT_STATUSES = {
    "success",
    "partial",
    "blocked",
    "failed",
    "skipped",
    "skipped_explicit",
}
SYNC_CHILD_TERMINAL_STATUSES = SYNC_TERMINAL_STATUSES | {"error"}
SYNC_CHILD_BLOCKED_STATUSES = {"blocked", "skipped", "skipped_explicit"}
SYNC_VALID_MODES = {"incremental", "full"}
SYNC_VALID_TARGETS = {"all", "foundation", "talent"}
SYNC_EXTRACT_ALL_DAGS = {
    "sap_successfactors": "sap_successfactors_extract_all",
}
SAP_SUCCESSFACTORS_CARTRIDGE = "sap_successfactors"
SAP_SUCCESSFACTORS_ENTITY_DAG_ID = "sap_successfactors_extract"
SAP_SUCCESSFACTORS_EXTRACT_ALL_DAG_ID = "sap_successfactors_extract_all"
SYNC_AGENTOPS_TOOLS = {
    "mcp-infra__simulation__monte_carlo_run",
    "mcp-infra__market_context_read",
    "mcp-infra__decision__orchestrate",
    "mcp-infra__wisdom_bits__run",
    "mcp-infra__control_room__raise_alert",
    "mcp-infra__control_room__raise_analysis_alert",
    "infra__simulation__monte_carlo_run",
    "infra__market_context_read",
    "infra__decision__orchestrate",
    "infra__wisdom_bits__run",
    "infra__control_room__raise_alert",
    "infra__control_room__raise_analysis_alert",
}


def sync_clean_mode(value: Any | None) -> str:
    try:
        return sync_progress.clean_sync_mode(value, valid_modes=SYNC_VALID_MODES)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def sync_clean_target(value: Any | None) -> str:
    try:
        return sync_progress.clean_sync_target(value, valid_targets=SYNC_VALID_TARGETS)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def airflow_run_id_fragment(value: str) -> str:
    fragment = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value or "")).strip("_")
    return (fragment or "entity")[:80]


def active_extract_run_payload(
    *,
    row: dict[str, Any],
    cartridge: str,
    entity: str,
    dag_id: str,
    conf: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    dag_run_id = row.get("airflow_dag_run_id") or row.get("run_id")
    state = normalize_airflow_state(row.get("status"))
    return {
        "triggered": False,
        "reused": True,
        "cartridge": cartridge,
        "entity": entity,
        "dag_id": dag_id,
        "job_id": dag_run_id,
        "run_id": dag_run_id,
        "dag_run_id": dag_run_id,
        "state": state,
        "reason": reason,
        "conf": conf,
    }


def pipeline_extract_all_mode_target(body: dict[str, Any]) -> tuple[str, str]:
    try:
        return sync_progress.pipeline_extract_all_mode_target(
            body,
            valid_modes=SYNC_VALID_MODES,
            valid_targets=SYNC_VALID_TARGETS,
        )
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


def pipeline_extract_all_run_id(
    *, cartridge: str, mode: str, target: str, conn_id: str | None, body: dict[str, Any]
) -> str:
    try:
        return sync_progress.extract_all_run_id(
            cartridge=cartridge,
            mode=mode,
            target=target,
            conn_id=conn_id,
            idempotency_key=body.get("idempotency_key") or body.get("request_id"),
        )
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


def pipeline_extract_all_public_response(result: dict[str, Any]) -> dict[str, Any]:
    return sync_progress.extract_all_public_response(result)


def sync_now_lock_key(
    *,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    tenant_id: Any | None,
    workspace_id: Any | None,
) -> str:
    return sync_progress.sync_now_lock_key(
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )


def active_sync_run_lookup_parts(
    *,
    cartridge: str,
    mode: str,
    target: str,
    has_mode: bool,
    has_extra: bool,
    has_started_at: bool,
) -> dict[str, Any]:
    clauses = [
        "cartridge_id=$1",
        f"entity='{SYNC_NOW_ENTITY}'",
        "(status IS NULL OR status <> ALL($2::text[]))",
    ]
    args: list[Any] = [cartridge, list(SYNC_TERMINAL_STATUSES)]
    if has_mode:
        args.append(mode)
        clauses.append(f"COALESCE(mode, ${len(args)})=${len(args)}")
    if has_extra:
        args.append(target)
        clauses.append(f"COALESCE(extra->>'target', 'all')=${len(args)}")
    if has_started_at:
        clauses.append(
            "(started_at IS NULL OR started_at > NOW() - INTERVAL '4 hours')"
        )
    order_sql = "started_at DESC NULLS LAST" if has_started_at else "run_id DESC"
    return {"clauses": clauses, "args": args, "order_sql": order_sql}


async def fetch_active_sync_run(
    *,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    user: dict[str, Any] | None,
    get_db_pool: Any,
    table_has_column: Any,
    pipeline_runs_scope_predicate: Any,
    scoped_db_for_user: Any,
    active_sync_run_lookup_parts_func: Any = active_sync_run_lookup_parts,
    logger_warning: Any | None = None,
) -> dict[str, Any] | None:
    pool = await get_db_pool()
    has_mode = await table_has_column("pipeline_runs", "mode", refresh=True)
    has_extra = await table_has_column("pipeline_runs", "extra", refresh=True)
    has_started_at = await table_has_column("pipeline_runs", "started_at", refresh=True)
    lookup = active_sync_run_lookup_parts_func(
        cartridge=cartridge,
        mode=mode,
        target=target,
        has_mode=has_mode,
        has_extra=has_extra,
        has_started_at=has_started_at,
    )
    clauses = lookup["clauses"]
    args = lookup["args"]
    scope_sql, scope_values = await pipeline_runs_scope_predicate(
        user, len(args) + 1, refresh_columns=True
    )
    order_sql = lookup["order_sql"]
    try:
        async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM pipeline_runs
                 WHERE {' AND '.join(clauses)}
                   {scope_sql}
                 ORDER BY {order_sql}
                 LIMIT 1
                """,
                *args,
                *scope_values,
            )
    except Exception:
        if logger_warning:
            logger_warning(
                "active sync run lookup failed for cartridge=%s mode=%s target=%s conn_id=%s",
                cartridge,
                mode,
                target,
                conn_id,
                exc_info=True,
            )
        return None
    return dict(row) if row else None


async def fetch_sync_run(
    *,
    cartridge: str,
    run_id: str,
    user: dict[str, Any] | None,
    get_db_pool: Any,
    pipeline_runs_scope_predicate: Any,
    scoped_db_for_user: Any,
    sync_now_entity: str = SYNC_NOW_ENTITY,
) -> dict[str, Any] | None:
    pool = await get_db_pool()
    scope_sql, scope_values = await pipeline_runs_scope_predicate(
        user, 3, refresh_columns=True
    )
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
        row = await conn.fetchrow(
            f"""
            SELECT *
              FROM pipeline_runs
             WHERE cartridge_id=$1
               AND run_id=$2
               AND entity='{sync_now_entity}'
               {scope_sql}
             LIMIT 1
            """,
            cartridge,
            run_id,
            *scope_values,
        )
    return dict(row) if row else None


async def fetch_sync_child_runs(
    *,
    cartridge: str,
    run_ids: list[str],
    user: dict[str, Any] | None,
    get_db_pool: Any,
    pipeline_runs_scope_predicate: Any,
    scoped_db_for_user: Any,
    refresh_dag_run_status: Any,
    sync_now_entity: str = SYNC_NOW_ENTITY,
) -> list[dict[str, Any]]:
    if not run_ids:
        return []
    pool = await get_db_pool()
    scope_sql, scope_values = await pipeline_runs_scope_predicate(
        user, 3, refresh_columns=True
    )
    async with scoped_db_for_user(pool, user) as (conn, _tenant_id, _workspace_id):
        rows = await conn.fetch(
            f"""
            SELECT *
              FROM pipeline_runs
             WHERE (
                    run_id = ANY($1::text[])
                 OR airflow_dag_run_id = ANY($1::text[])
               )
               AND cartridge_id=$2
               AND entity <> '{sync_now_entity}'
               {scope_sql}
             ORDER BY started_at ASC
            """,
            run_ids,
            cartridge,
            *scope_values,
        )
    refreshed: list[dict[str, Any]] = []
    for row in rows:
        refreshed.append(await refresh_dag_run_status(dict(row), user))
    return refreshed


def normalize_sync_step_payload(step: dict[str, Any]) -> dict[str, Any]:
    return sync_progress.normalize_sync_step_payload(
        step,
        recompute_statuses=SYNC_STEP_RECOMPUTE_PERCENT_STATUSES,
    )


def initial_sync_steps() -> list[dict[str, Any]]:
    return sync_progress.initial_sync_steps()


def merge_sync_steps(
    current: list[dict[str, Any]] | None,
    updates: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return sync_progress.merge_sync_steps(
        current,
        updates,
        normalizer=normalize_sync_step_payload,
        initial_steps_factory=initial_sync_steps,
    )


def sync_start_step_updates() -> dict[str, dict[str, Any]]:
    return {
        "connection": {
            "label": "Conexión",
            "status": "running",
            "detail": "Validando scope y conexión del cartucho.",
        }
    }


def sync_dataset_seed_failure_step_updates(
    message: str,
) -> dict[str, dict[str, Any]]:
    return {
        "connection": {
            "label": "Conexión",
            "status": "success",
            "detail": "Scope y conexión aceptados por el pipeline.",
        },
        "silver_gold": {
            "label": "Silver/Gold",
            "status": "failed",
            "detail": "No se pudieron preparar los refinamientos del workspace.",
            "error": message[:300],
            "completed": 0,
            "total": 1,
        },
        "control_room": {
            "label": "Control Room",
            "status": "failed",
            "detail": "Sin Silver/Gold preparados no se puede refrescar Control Room.",
            "completed": 0,
            "total": 1,
        },
        "agents_intelligence": {
            "label": "Agentes/IA",
            "status": "failed",
            "detail": "Sin materialización no se ejecutan monitores.",
            "completed": 0,
            "total": 1,
        },
    }


def sync_running_extra(
    *,
    mode: str,
    target: str,
    conn_id: str | None,
    request_id: str | None,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "mode": mode,
        "target": target,
        "conn_id": conn_id,
        "request_id": request_id,
        "steps": steps,
        "triggered_entities": [],
        "errors": [],
        "control_room_ready": False,
    }


def sync_dataset_seed_failure_extra(
    *,
    mode: str,
    target: str,
    conn_id: str | None,
    request_id: str | None,
    steps: list[dict[str, Any]],
    message: str,
) -> dict[str, Any]:
    reason = "packaged_dataset_seed_failed"
    return {
        "mode": mode,
        "target": target,
        "conn_id": conn_id,
        "request_id": request_id,
        "steps": steps,
        "triggered_entities": [],
        "errors": [
            {
                "entity": "__dataset_seed__",
                "error": message,
                "reason": reason,
            }
        ],
        "control_room_ready": False,
        "dataset_seed": {
            "status": "failed",
            "reason": reason,
            "error": message,
        },
    }


def sync_extract_all_trigger_extra(
    *,
    mode: str,
    target: str,
    conn_id: str | None,
    request_id: str | None,
    steps: list[dict[str, Any]],
    triggered_entities: list[Any],
    errors: list[dict[str, Any]],
    result: dict[str, Any],
    dataset_seed: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "mode": mode,
        "target": target,
        "conn_id": conn_id,
        "request_id": request_id,
        "steps": steps,
        "triggered_entities": triggered_entities,
        "errors": errors,
        "control_room_ready": False,
        "extract_all_result": result,
        "trigger_strategy": result.get("trigger_strategy") or "fanout",
        "dataset_seed": dataset_seed,
    }


def sync_extract_all_result_state(result: dict[str, Any]) -> dict[str, Any]:
    triggered_entities = (
        result.get("triggered") if isinstance(result.get("triggered"), list) else []
    )
    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    bronze_status = (
        "running" if triggered_entities else "failed" if errors else "partial"
    )
    return {
        "triggered_entities": triggered_entities,
        "errors": errors,
        "bronze_status": bronze_status,
    }


def sync_extract_all_trigger_step_updates(
    *,
    triggered_entities: list[Any],
    errors: list[dict[str, Any]],
    attempts: int,
) -> dict[str, dict[str, Any]]:
    bronze_status = (
        "running" if triggered_entities else "failed" if errors else "partial"
    )
    return {
        "connection": {
            "label": "Conexión",
            "status": "success" if triggered_entities or not errors else "partial",
            "detail": "El pipeline aceptó la sincronización."
            if triggered_entities
            else "El pipeline respondió sin entidades disparadas.",
            "attempts": attempts,
        },
        "bronze": {
            "label": "Bronze",
            "status": bronze_status,
            "detail": f"{len(triggered_entities)} entidades disparadas; {len(errors)} errores iniciales.",
            "attempts": attempts,
        },
        "silver_gold": {
            "label": "Silver/Gold",
            "status": "queued" if triggered_entities else "failed",
            "detail": "Airflow encadenará materialización downstream."
            if triggered_entities
            else "No hay extracción base para materializar.",
        },
        "control_room": {
            "label": "Control Room",
            "status": "queued" if triggered_entities else "failed",
            "detail": "Esperando Gold para refrescar señales."
            if triggered_entities
            else "No hay Gold nuevo disponible.",
            "completed": 0,
            "total": 1,
        },
        "agents_intelligence": {
            "label": "Agentes/IA",
            "status": "queued" if triggered_entities else "failed",
            "detail": "Se ejecutarán monitores reales al terminar Control Room."
            if triggered_entities
            else "No hay datos base para ejecutar monitores.",
            "completed": 0,
            "total": 1,
        },
    }


def sync_extract_all_error_message(errors: list[dict[str, Any]]) -> str | None:
    message = "; ".join(
        str(item.get("error") or "") for item in errors[:3] if isinstance(item, dict)
    )
    return message or None


def sync_run_working_state(row: dict[str, Any]) -> dict[str, Any]:
    extra = sync_extra_from_row(row)
    steps = (
        extra.get("steps")
        if isinstance(extra.get("steps"), list)
        else initial_sync_steps()
    )
    triggered = (
        extra.get("triggered_entities")
        if isinstance(extra.get("triggered_entities"), list)
        else []
    )
    errors = extra.get("errors") if isinstance(extra.get("errors"), list) else []
    child_run_ids = [
        str(item.get("dag_run_id") or item.get("job_id") or "").strip()
        for item in triggered
        if isinstance(item, dict)
        and str(item.get("dag_run_id") or item.get("job_id") or "").strip()
    ]
    return {
        "extra": extra,
        "steps": steps,
        "triggered": triggered,
        "errors": errors,
        "child_run_ids": child_run_ids,
    }


def sync_child_runtime_state(
    *,
    row: dict[str, Any],
    child_rows: list[dict[str, Any]],
    child_run_ids: list[str],
    triggered: list[Any],
    errors: list[dict[str, Any]],
    stale_after_seconds: int,
) -> dict[str, Any]:
    gold_refresh_summary = sync_child_gold_refresh_summary(child_rows)
    child_progress = sync_progress.sync_child_progress_rows(
        child_rows,
        aggregate_entity=SYNC_AGGREGATE_ENTITY,
        sync_now_entity=SYNC_NOW_ENTITY,
    )
    aggregate_child_rows = child_progress["aggregate_child_rows"]
    aggregate_payload_ready = child_progress["aggregate_payload_ready"]
    entity_child_rows = child_progress["entity_child_rows"]
    aggregate_summary_pending = child_progress["aggregate_summary_pending"]
    progress_rows = child_progress["progress_rows"]
    child_counts = sync_progress.sync_child_status_counts(
        progress_rows,
        blocked_statuses=SYNC_CHILD_BLOCKED_STATUSES,
        terminal_statuses=SYNC_CHILD_TERMINAL_STATUSES,
    )
    running_children = bool(child_counts["running"])
    failed_children = int(child_counts["failed"])
    success_children = int(child_counts["success"])
    partial_children = int(child_counts["partial"])
    blocked_children = int(child_counts["blocked"])
    terminal_children = int(child_counts["terminal"])
    entity_summary = sync_step_entity_summary(progress_rows)
    stale_running = (
        str(row.get("status") or "").lower() not in SYNC_TERMINAL_STATUSES
        and running_children
        and not success_children
        and (sync_run_age_seconds(row) or 0) > stale_after_seconds
    )
    if stale_running:
        running_children = False
        failed_children = max(failed_children, 1)
        errors = [
            *errors,
            {
                "entity": SYNC_NOW_ENTITY,
                "status_code": 504,
                "error": "Airflow sync run timed out before completing; start a new sync.",
            },
        ]
    return {
        "gold_refresh_summary": gold_refresh_summary,
        "aggregate_child_rows": aggregate_child_rows,
        "aggregate_payload_ready": aggregate_payload_ready,
        "entity_child_rows": entity_child_rows,
        "aggregate_summary_pending": aggregate_summary_pending,
        "progress_rows": progress_rows,
        "running_children": running_children,
        "failed_children": failed_children,
        "success_children": success_children,
        "partial_children": partial_children,
        "blocked_children": blocked_children,
        "terminal_children": terminal_children,
        "entity_summary": entity_summary,
        "errors": errors,
        "child_total": max(
            len(entity_child_rows),
            len(progress_rows),
            len(child_run_ids),
            len(triggered),
            1,
        ),
    }


def sync_materialization_state(
    pipeline_rows: list[dict[str, Any]],
    gold_refresh_summary: dict[str, Any],
) -> dict[str, Any]:
    materialization = sync_progress.sync_pipeline_materialization_summary(
        pipeline_rows,
        gold_refresh_summary,
    )
    return {
        "materialization": materialization,
        "bronze_ready": int(materialization["bronze_ready"]),
        "silver_ready": int(materialization["silver_ready"]),
        "gold_ready": int(materialization["gold_ready"]),
        "gold_total": int(materialization["gold_total"]),
        "gold_partial": bool(materialization["gold_partial"]),
    }


def sync_core_step_updates(
    *,
    triggered: list[Any],
    child_rows: list[dict[str, Any]],
    child_runtime: dict[str, Any],
    materialization_state: dict[str, Any],
    errors: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    bronze_ready = int(materialization_state["bronze_ready"])
    silver_ready = int(materialization_state["silver_ready"])
    gold_ready = int(materialization_state["gold_ready"])
    gold_total = int(materialization_state["gold_total"])
    gold_partial = bool(materialization_state["gold_partial"])
    running_children = bool(child_runtime["running_children"])
    failed_children = int(child_runtime["failed_children"])
    success_children = int(child_runtime["success_children"])
    partial_children = int(child_runtime["partial_children"])
    blocked_children = int(child_runtime["blocked_children"])
    terminal_children = int(child_runtime["terminal_children"])
    progress_rows = child_runtime["progress_rows"]
    entity_summary = child_runtime["entity_summary"]

    updates: dict[str, dict[str, Any]] = {
        "connection": sync_progress.sync_connection_step_update(
            triggered=triggered,
            child_rows=child_rows,
            bronze_ready=bronze_ready,
        )
    }
    bronze_update = sync_progress.sync_bronze_step_update(
        running_children=running_children,
        aggregate_summary_pending=bool(child_runtime["aggregate_summary_pending"]),
        progress_count=len(progress_rows),
        failed_children=failed_children,
        success_children=success_children,
        partial_children=partial_children,
        blocked_children=blocked_children,
        errors=errors,
        child_done=terminal_children,
        child_total=int(child_runtime["child_total"]),
        bronze_ready=bronze_ready,
        entity_summary=entity_summary,
    )
    if bronze_update:
        updates["bronze"] = bronze_update

    silver_gold_update = sync_progress.sync_silver_gold_step_update(
        bronze_status=str(updates.get("bronze", {}).get("status") or ""),
        running_children=running_children,
        gold_total=gold_total,
        silver_ready=silver_ready,
        gold_ready=gold_ready,
        failed_children=failed_children,
        partial_children=partial_children,
        blocked_children=blocked_children,
        gold_partial=gold_partial,
        errors=errors,
    )
    if silver_gold_update:
        updates["silver_gold"] = silver_gold_update
    return updates


def sync_status_from_steps(steps: list[dict[str, Any]]) -> str:
    return sync_progress.sync_status_from_steps(steps)


def sync_entity_idempotency_key(
    base_key: object | None, entity: object | None
) -> str | None:
    return sync_progress.sync_entity_idempotency_key(base_key, entity)


def normalize_sync_now_request_id(value: object | None) -> str | None:
    try:
        return sync_progress.normalize_sync_now_request_id(value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def sync_now_run_id_from_request_id(
    *, cartridge: str, request_id: str | None, lock_key: str
) -> str:
    return sync_progress.sync_now_run_id_from_request_id(
        cartridge=cartridge,
        request_id=request_id,
        lock_key=lock_key,
    )


def sync_run_age_seconds(row: dict[str, Any]) -> float | None:
    return sync_progress.sync_run_age_seconds(row)


def sync_extra_from_row(row: dict[str, Any] | None) -> dict[str, Any]:
    return sync_progress.sync_extra_from_row(row)


def sync_child_reason(row: dict[str, Any]) -> str | None:
    return sync_progress.sync_child_reason(row)


def sync_step_entity_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return sync_progress.sync_step_entity_summary(
        rows,
        aggregate_entity=SYNC_AGGREGATE_ENTITY,
        sync_now_entity=SYNC_NOW_ENTITY,
        blocked_statuses=SYNC_CHILD_BLOCKED_STATUSES,
    )


def sync_public_payload(row: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    return sync_progress.public_sync_payload(
        row,
        extra,
        terminal_statuses=SYNC_TERMINAL_STATUSES,
        normalizer=normalize_sync_step_payload,
        initial_steps_factory=initial_sync_steps,
    )


def inactive_sync_run_payload(
    *, cartridge: str, mode: str, target: str, conn_id: str | None = None
) -> dict[str, Any]:
    return sync_progress.inactive_sync_run_payload(
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
    )


def sync_run_needs_final_reconcile(row: dict[str, Any], extra: dict[str, Any]) -> bool:
    return sync_progress.sync_run_needs_final_reconcile(
        row,
        extra,
        terminal_statuses=SYNC_TERMINAL_STATUSES,
        initial_step_count=len(initial_sync_steps()),
        aggregate_entity=SYNC_AGGREGATE_ENTITY,
    )


def sync_errors_retryable(errors: list[dict[str, Any]]) -> bool:
    return sync_progress.sync_errors_retryable(errors)


async def run_sync_extract_all_with_retries(
    *,
    cartridge: str,
    mode: str,
    target: str,
    conn_id: str | None,
    run_id: str,
    extract_body: dict[str, Any],
    user: dict[str, Any],
    trigger_sync_aggregate_extract_all: Any,
    call_with_optional_user: Any,
    api_pipeline_extract_all: Any,
    retryable_errors: Any = sync_errors_retryable,
    sleep: Any | None = None,
    max_attempts: int = 3,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    attempts = 0
    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        aggregate_result = await trigger_sync_aggregate_extract_all(
            cartridge=cartridge,
            mode=mode,
            target=target,
            conn_id=conn_id,
            run_id=run_id,
            user=user,
        )
        result = (
            aggregate_result
            if aggregate_result is not None
            else await call_with_optional_user(
                api_pipeline_extract_all,
                cartridge,
                extract_body,
                user=user,
            )
        )
        errors = result.get("errors") if isinstance(result.get("errors"), list) else []
        triggered = (
            result.get("triggered") if isinstance(result.get("triggered"), list) else []
        )
        if triggered or not retryable_errors(errors) or attempt == max_attempts:
            break
        if sleep is not None:
            await sleep(2 * attempt)
    return {"result": result, "attempts": attempts}


async def maybe_trigger_aggregate_extract_all(
    *,
    cartridge: str,
    body: dict[str, Any],
    user: dict[str, Any] | None,
    sync_extract_all_dags: dict[str, str],
    pipeline_extract_all_mode_target_func: Any,
    resolve_pipeline_sync_conn_id_func: Any,
    pipeline_extract_all_run_id_func: Any,
    trigger_sync_aggregate_extract_all_func: Any,
    pipeline_extract_all_public_response_func: Any,
) -> dict[str, Any] | None:
    if cartridge not in sync_extract_all_dags:
        return None
    mode, target = pipeline_extract_all_mode_target_func(body)
    conn_id = await resolve_pipeline_sync_conn_id_func(
        cartridge,
        body.get("conn_id") or body.get("connection_id"),
        user,
    )
    run_id = pipeline_extract_all_run_id_func(
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
        body=body,
    )
    result = await trigger_sync_aggregate_extract_all_func(
        cartridge=cartridge,
        mode=mode,
        target=target,
        conn_id=conn_id,
        run_id=run_id,
        user=user,
    )
    if result is None:
        return None
    return pipeline_extract_all_public_response_func(result)


def sync_child_gold_refresh_summary(child_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return sync_progress.child_gold_refresh_summary(child_rows)


def sync_gold_refresh_dataset_names(gold_refresh_summary: dict[str, Any]) -> list[str]:
    return sync_progress.gold_refresh_dataset_names(gold_refresh_summary)


def sync_control_room_gold_refresh_terminal(payload: Any) -> bool:
    return sync_progress.control_room_gold_refresh_terminal(payload)


async def build_sync_run_status(
    *,
    cartridge: str,
    row: dict[str, Any],
    user: dict[str, Any] | None,
    sync_child_runs: Any,
    call_pipeline: Any,
    run_control_room_gold_refresh: Any,
    run_control_room_status: Any,
    run_agentops_status: Any,
    upsert_sync_run: Any,
    fetch_sync_run_func: Any,
    control_room_cache_invalidate: Any,
    logger_debug: Any,
    stale_after_seconds: int,
    persist_control_room_state: bool = False,
) -> dict[str, Any]:
    working_state = sync_run_working_state(row)
    extra = working_state["extra"]
    steps = working_state["steps"]
    triggered = working_state["triggered"]
    errors = working_state["errors"]
    child_run_ids = working_state["child_run_ids"]
    child_rows = await sync_child_runs(
        cartridge=cartridge, run_ids=child_run_ids, user=user
    )
    child_runtime = sync_child_runtime_state(
        row=row,
        child_rows=child_rows,
        child_run_ids=child_run_ids,
        triggered=triggered,
        errors=errors,
        stale_after_seconds=stale_after_seconds,
    )
    gold_refresh_summary = child_runtime["gold_refresh_summary"]
    aggregate_payload_ready = child_runtime["aggregate_payload_ready"]
    entity_child_rows = child_runtime["entity_child_rows"]
    aggregate_summary_pending = child_runtime["aggregate_summary_pending"]
    running_children = child_runtime["running_children"]
    entity_summary = child_runtime["entity_summary"]
    errors = child_runtime["errors"]

    try:
        pipeline_payload = await call_pipeline(cartridge, user=user)
        pipeline_rows = pipeline_payload.get("pipeline") or []
    except Exception as exc:
        pipeline_rows = []
        errors = [
            *errors,
            {"entity": "__pipeline__", "status_code": 503, "error": str(exc)},
        ]

    materialization_state = sync_materialization_state(
        pipeline_rows,
        gold_refresh_summary,
    )
    bronze_ready = materialization_state["bronze_ready"]
    silver_ready = materialization_state["silver_ready"]
    gold_ready = materialization_state["gold_ready"]
    control_room_gold_refresh = (
        dict(extra.get("control_room_gold_refresh"))
        if isinstance(extra.get("control_room_gold_refresh"), dict)
        else {}
    )
    if (
        cartridge == SAP_SUCCESSFACTORS_CARTRIDGE
        and gold_ready
        and not running_children
        and not sync_control_room_gold_refresh_terminal(control_room_gold_refresh)
    ):
        control_room_gold_refresh = await run_control_room_gold_refresh(
            cartridge=cartridge,
            row=row,
            child_rows=child_rows,
            gold_refresh_summary=gold_refresh_summary,
            user=user,
        )

    updates = sync_core_step_updates(
        triggered=triggered,
        child_rows=child_rows,
        child_runtime=child_runtime,
        materialization_state=materialization_state,
        errors=errors,
    )

    control_room_status = await run_control_room_status(
        cartridge=cartridge,
        bronze_ready=bronze_ready,
        silver_ready=silver_ready,
        gold_ready=gold_ready,
        running_children=running_children,
        control_room_gold_refresh=control_room_gold_refresh,
        user=user,
        persist_dashboard_state=persist_control_room_state,
    )
    control_room_ready = bool(control_room_status["ready"])
    control_room_checked_at = control_room_status["checked_at"]
    control_room_snapshot = control_room_status["snapshot"]
    updates["control_room"] = control_room_status["update"]

    agentops_refresh = (
        dict(extra.get("agentops_refresh"))
        if isinstance(extra.get("agentops_refresh"), dict)
        else {}
    )
    agentops_status = await run_agentops_status(
        cartridge=cartridge,
        sync_run_id=str(row["run_id"]),
        running_children=running_children,
        bronze_ready=bronze_ready,
        silver_ready=silver_ready,
        gold_ready=gold_ready,
        control_room_update=updates.get("control_room", {}),
        agentops_refresh=agentops_refresh,
        user=user,
    )
    agentops_refresh = agentops_status["agentops_refresh"]
    updates["agents_intelligence"] = agentops_status["update"]

    steps = merge_sync_steps(steps, updates)
    status = sync_status_from_steps(steps)
    updated_extra = sync_progress.sync_updated_extra(
        steps=steps,
        triggered=triggered,
        errors=errors,
        control_room_ready=control_room_ready,
        control_room_checked_at=control_room_checked_at,
        control_room_snapshot=control_room_snapshot,
        agentops_refresh=agentops_refresh,
        gold_refresh_summary=gold_refresh_summary,
        control_room_gold_refresh=control_room_gold_refresh,
        aggregate_payload_ready=aggregate_payload_ready,
        aggregate_summary_pending=aggregate_summary_pending,
        child_run_count=len(child_rows),
        entity_child_run_count=len(entity_child_rows),
        entity_summary=entity_summary,
        previous_extra=extra,
        row_mode=str(row.get("mode") or "") or None,
    )
    await upsert_sync_run(
        run_id=str(row["run_id"]),
        cartridge=cartridge,
        mode=str(row.get("mode") or updated_extra["mode"]),
        status=status,
        user=user,
        extra=updated_extra,
        error_message=sync_progress.sync_run_error_message(errors),
    )
    if status in SYNC_TERMINAL_STATUSES:
        try:
            control_room_cache_invalidate(user)
        except Exception:
            logger_debug(
                "Could not invalidate Control Room cache after sync",
                exc_info=True,
            )
    refreshed = await fetch_sync_run_func(
        cartridge=cartridge, run_id=str(row["run_id"]), user=user
    )
    return sync_public_payload(refreshed or row, {**extra, **updated_extra})
