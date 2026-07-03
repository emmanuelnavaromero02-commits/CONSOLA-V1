"""Small helpers for pipeline run status and Airflow log metadata."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


AIRFLOW_LOG_TASKS_BY_DAG = {
    "sap_successfactors_extract": ("trigger_extract",),
    "sap_successfactors_extract_all": ("trigger_extract_all",),
}


def parse_iso_datetime(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def duration_seconds(started_at, finished_at) -> float | None:
    start = parse_iso_datetime(str(started_at)) if started_at else None
    finish = parse_iso_datetime(str(finished_at)) if finished_at else None
    if not start or not finish:
        return None
    return max((finish - start).total_seconds(), 0.0)


def normalize_airflow_state(state: str | None) -> str:
    normalized = (state or "unknown").lower()
    if normalized in {"queued", "running", "success", "failed", "partial"}:
        return normalized
    return "unknown"


def pipeline_run_extra(row: dict | None) -> dict:
    if not isinstance(row, dict):
        return {}
    extra = row.get("extra")
    if isinstance(extra, dict):
        return extra
    if isinstance(extra, str) and extra.strip():
        try:
            parsed = json.loads(extra)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def pipeline_downstream_status(extra: dict, key: str) -> str | None:
    value = extra.get(key)
    if isinstance(value, dict):
        status = str(value.get("status") or "").strip().lower()
        return status or None
    return None


def pipeline_is_zero_count(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return int(value) == 0
    if isinstance(value, str):
        try:
            return int(value.strip()) == 0
        except ValueError:
            return False
    return False


def airflow_task_id(task: Any) -> str | None:
    if isinstance(task, dict):
        value = task.get("task_id")
    else:
        value = getattr(task, "task_id", None)
    value = str(value or "").strip()
    return value or None


def airflow_log_task_ids(
    dag_id: str | None, tasks: list[dict] | None = None
) -> list[str]:
    dag_id = str(dag_id or "").strip()
    preferred = list(AIRFLOW_LOG_TASKS_BY_DAG.get(dag_id, ()))
    available = [
        task_id
        for task_id in (airflow_task_id(task) for task in (tasks or []))
        if task_id
    ]
    if available:
        if preferred:
            return [task_id for task_id in preferred if task_id in available]
        return available
    if preferred:
        return preferred
    return ["extract"]


def airflow_log_attempt(
    dag_id: str | None,
    dag_run_id: str | None,
    task_ids: list[str],
    tasks: list[dict] | None = None,
) -> dict:
    return {
        "dag_id": dag_id,
        "dag_run_id": dag_run_id,
        "task_ids": task_ids,
        "available_task_ids": [
            task_id
            for task_id in (airflow_task_id(task) for task in (tasks or []))
            if task_id
        ],
    }

