from __future__ import annotations

from typing import Any


def gold_refresh_airflow_run_id(
    row: dict[str, Any],
    child_rows: list[dict[str, Any]],
    *,
    aggregate_entity: str,
) -> str:
    for item in child_rows:
        if str(item.get("entity") or "") != aggregate_entity:
            continue
        value = str(
            item.get("airflow_dag_run_id") or item.get("run_id") or ""
        ).strip()
        if value:
            return value
    return str(row.get("run_id") or "").strip() or "sync-now"


def gold_refresh_skipped_payload(checked_at: str) -> dict[str, Any]:
    return {
        "status": "skipped",
        "checked_at": checked_at,
        "reason": "No Gold datasets were materialized successfully.",
        "datasets": [],
    }


def gold_refresh_missing_scope_payload(
    *,
    checked_at: str,
    datasets: list[str],
) -> dict[str, Any]:
    return {
        "status": "failed",
        "checked_at": checked_at,
        "reason": "Missing tenant/workspace scope for Control Room Gold refresh.",
        "datasets": datasets,
    }


def gold_refresh_run_ref(
    *,
    workspace_id: str,
    cartridge: str,
    airflow_dag_run_id: str,
) -> str:
    return f"gold-refresh:{workspace_id}:{cartridge}:{airflow_dag_run_id}"


def gold_refresh_intelligence_payload(
    *,
    cartridge: str,
    datasets: list[str],
    row: dict[str, Any],
    airflow_dag_run_id: str,
    run_ref: str,
    gold_refresh_summary: dict[str, Any],
) -> dict[str, Any]:
    finished_at = row.get("finished_at")
    return {
        "cartridge_id": cartridge,
        "datasets": datasets,
        "include_external": False,
        "dry_run": False,
        "run_mode": "gold_refresh",
        "run_ref": run_ref,
        "horizon_days": [7, 21],
        "metadata": {
            "trigger": "sync_now_gold_refresh",
            "pipeline_run_id": row.get("run_id"),
            "airflow_dag_run_id": airflow_dag_run_id,
            "materialization_status": gold_refresh_summary.get("status") or "partial",
            "datasets_received": datasets,
            "finished_at": finished_at.isoformat()
            if hasattr(finished_at, "isoformat")
            else finished_at,
        },
    }


def gold_refresh_error_payload(
    *,
    checked_at: str,
    run_ref: str,
    datasets: list[str],
    exc: Exception,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "checked_at": checked_at,
        "ok": False,
        "run_ref": run_ref,
        "datasets": datasets,
        "error": f"{type(exc).__name__}: {exc}"[:500],
    }


def gold_refresh_result_payload(
    *,
    checked_at: str,
    run_ref: str,
    datasets: list[str],
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    result_payload = result if isinstance(result, dict) else {}
    signals = len(result_payload.get("signals") or [])
    skipped = len(result_payload.get("skipped") or [])
    engine_status = str(result_payload.get("status") or "").strip().lower()
    public_status = engine_status or ("completed" if signals else "not_ready")
    return {
        "status": "success" if public_status in {"completed", "success"} else "partial",
        "checked_at": checked_at,
        "ok": True,
        "run_ref": result_payload.get("run_ref") or run_ref,
        "intelligence_run_id": result_payload.get("intelligence_run_id"),
        "engine_status": public_status,
        "idempotent": bool(result_payload.get("idempotent")),
        "signals": signals,
        "skipped": skipped,
        "dataset_unavailable_count": result_payload.get("dataset_unavailable_count", 0),
        "insufficient_history_count": result_payload.get("insufficient_history_count", 0),
        "skipped_counts": result_payload.get("skipped_counts") or {},
        "datasets": datasets,
    }
