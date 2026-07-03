"""Small helpers for pipeline run status and Airflow log metadata."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
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


def pipeline_freshness_status(
    dt_str: str | None, *, threshold_h: int = 24, now: datetime | None = None
) -> str:
    if not dt_str:
        return "never"
    try:
        dt = datetime.fromisoformat(str(dt_str).replace("Z", "+00:00"))
        age = (now or datetime.now(timezone.utc)) - dt
        return "fresh" if age < timedelta(hours=threshold_h) else "stale"
    except Exception:
        return "unknown"


def pipeline_dataset_status(
    dataset: dict, *, failed: bool, threshold_h: int = 24, now: datetime | None = None
) -> str:
    if failed:
        return "stale"
    if pipeline_is_zero_count(dataset.get("row_count")):
        return "empty"
    return pipeline_freshness_status(
        dataset.get("last_refresh"), threshold_h=threshold_h, now=now
    )


def pipeline_silver_datasets_by_source(silver_datasets: list[dict]) -> dict[str, list[dict]]:
    by_source: dict[str, list[dict]] = {}
    for dataset in silver_datasets:
        for source in dataset.get("sources") or []:
            by_source.setdefault(source, []).append(dataset)
    return by_source


def pipeline_gold_dependencies_for_silver(
    silver_name: str, gold_datasets: list[dict], *, cartridge: str
) -> list[dict]:
    deps = []
    silver_lower = silver_name.lower()
    cartridge_lower = cartridge.lower()
    for gold_dataset in gold_datasets:
        sql = gold_dataset.get("sql_def") or gold_dataset.get("sql") or ""
        sources = [
            str(source)
            for source in (gold_dataset.get("sources") or [])
            if str(source).strip()
        ]
        haystack = "\n".join([sql, *sources])
        normalized = haystack.replace("\\", "/").lower()
        if re.search(
            rf"\bsilver_{re.escape(silver_name)}\b", sql, re.IGNORECASE
        ) or (f"silver/{cartridge_lower}/{silver_lower}" in normalized):
            deps.append(gold_dataset)
    return deps


def pipeline_jobs_by_entity(jobs: list[dict]) -> dict[str, dict]:
    by_entity: dict[str, dict] = {}
    for job in jobs:
        entity = (job.get("args") or {}).get("entity") or ""
        if not entity or entity in by_entity:
            continue
        by_entity[entity] = job
    return by_entity


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
