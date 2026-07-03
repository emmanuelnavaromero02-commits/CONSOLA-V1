"""SuccessFactors extraction reservation helpers."""

from __future__ import annotations

import json
from typing import Any


def reservation_applies(*, cartridge: str, dag_id: str, expected_cartridge: str, entity_dag_id: str) -> bool:
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
