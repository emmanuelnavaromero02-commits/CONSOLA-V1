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
    "mcp-infra__decision__orchestrate",
    "mcp-infra__wisdom_bits__run",
    "mcp-infra__control_room__raise_alert",
    "mcp-infra__control_room__raise_analysis_alert",
    "infra__simulation__monte_carlo_run",
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


def sync_child_gold_refresh_summary(child_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return sync_progress.child_gold_refresh_summary(child_rows)


def sync_gold_refresh_dataset_names(gold_refresh_summary: dict[str, Any]) -> list[str]:
    return sync_progress.gold_refresh_dataset_names(gold_refresh_summary)


def sync_control_room_gold_refresh_terminal(payload: Any) -> bool:
    return sync_progress.control_room_gold_refresh_terminal(payload)
