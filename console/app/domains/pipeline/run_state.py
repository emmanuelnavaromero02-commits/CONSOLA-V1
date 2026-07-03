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


def pipeline_silver_gold_nodes(
    *,
    source: str,
    silver_by_source: dict[str, list[dict]],
    gold_datasets: list[dict],
    failed: bool,
    cartridge: str,
    now: datetime | None = None,
) -> tuple[list[dict], list[dict]]:
    silver_nodes: list[dict] = []
    gold_nodes: list[dict] = []
    for dataset in silver_by_source.get(source, []):
        silver_nodes.append(
            {
                "name": dataset["name"],
                "layer": dataset.get("layer", "silver"),
                "row_count": dataset.get("row_count"),
                "last_refresh": dataset.get("last_refresh"),
                "status": pipeline_dataset_status(
                    dataset,
                    failed=failed,
                    threshold_h=24,
                    now=now,
                ),
            }
        )
        for gold_dataset in pipeline_gold_dependencies_for_silver(
            dataset["name"], gold_datasets, cartridge=cartridge
        ):
            if any(node["name"] == gold_dataset["name"] for node in gold_nodes):
                continue
            gold_nodes.append(
                {
                    "name": gold_dataset["name"],
                    "layer": "gold",
                    "row_count": gold_dataset.get("row_count"),
                    "last_refresh": gold_dataset.get("last_refresh"),
                    "status": pipeline_dataset_status(
                        gold_dataset,
                        failed=failed,
                        threshold_h=24,
                        now=now,
                    ),
                }
            )
    return silver_nodes, gold_nodes


def pipeline_jobs_by_entity(jobs: list[dict]) -> dict[str, dict]:
    by_entity: dict[str, dict] = {}
    for job in jobs:
        entity = (job.get("args") or {}).get("entity") or ""
        if not entity or entity in by_entity:
            continue
        by_entity[entity] = job
    return by_entity


def pipeline_bronze_date_count(
    dag_run: dict | None, last_job: dict | None
) -> tuple[str | None, Any | None]:
    if dag_run:
        finished_at = dag_run.get("finished_at")
        bronze_date = str(finished_at)[:10] if finished_at else None
        return bronze_date, dag_run.get("record_count")
    if last_job and last_job.get("status") == "done":
        result = last_job.get("result") or {}
        bronze_date = (last_job.get("finished_at") or last_job.get("created_at") or "")[
            :10
        ]
        bronze_count = result.get("record_count") or result.get("total_records")
        return bronze_date, bronze_count
    return None, None


def pipeline_bronze_status(
    *,
    dag_run: dict | None,
    dag_status: str | None,
    bronze_date: str | None,
    bronze_count: Any,
    has_partial_reasons: bool,
    now: datetime | None = None,
) -> str:
    if dag_run and dag_run.get("status") == "failed" and not bronze_date:
        return "error"
    if dag_status in {"queued", "running"} and not bronze_date:
        return "running"
    if dag_status == "partial":
        return "partial"
    if bronze_date and pipeline_is_zero_count(bronze_count):
        return "empty"
    if bronze_date:
        return pipeline_freshness_status(
            bronze_date + "T00:00:00+00:00",
            now=now,
        )
    if has_partial_reasons:
        return "unknown"
    return "never"


def pipeline_airflow_last_run_info(dag_run: dict) -> tuple[dict, str]:
    finished_at = dag_run.get("finished_at")
    dag_status = normalize_airflow_state(dag_run.get("status"))
    dag_run_id = dag_run.get("airflow_dag_run_id") or dag_run.get("run_id")
    extra = pipeline_run_extra(dag_run)
    silver_refresh_status = pipeline_downstream_status(extra, "silver_refresh")
    return (
        {
            "source": "airflow",
            "dag_id": dag_run.get("dag_id"),
            "dag_run_id": dag_run_id,
            "run_id": dag_run_id,
            "status": dag_status,
            "result_status": extra.get("result_status"),
            "silver_refresh_status": silver_refresh_status,
            "empty_result": bool(extra.get("empty_result")),
            "extra": extra,
            "mode": dag_run.get("mode"),
            "triggered_at": str(dag_run.get("started_at", ""))
            if dag_run.get("started_at")
            else None,
            "started_at": str(dag_run.get("started_at", ""))
            if dag_run.get("started_at")
            else None,
            "finished_at": str(finished_at) if finished_at else None,
            "duration_sec": float(dag_run.get("duration_seconds"))
            if dag_run.get("duration_seconds") is not None
            else None,
            "error": dag_run.get("error_message"),
        },
        dag_status,
    )


def pipeline_job_last_run_info(last_job: dict) -> dict:
    return {
        "source": "jobs",
        "job_id": last_job["job_id"],
        "status": last_job["status"],
        "finished_at": last_job.get("finished_at") or last_job.get("created_at"),
        "message": last_job.get("message"),
    }


def pipeline_bronze_last_run_info(
    *, bronze_date: str, bronze_count: Any, mode: str | None
) -> dict:
    return {
        "source": "bronze",
        "status": "empty" if pipeline_is_zero_count(bronze_count) else "success",
        "mode": mode,
        "finished_at": f"{bronze_date}T00:00:00+00:00",
        "message": "Bronze materializado; corrida no registrada en pipeline_runs",
        "record_count": bronze_count,
    }


def pipeline_legacy_last_job(last_run_info: dict | None) -> dict | None:
    if not last_run_info:
        return None
    return {
        "job_id": last_run_info.get("job_id"),
        "dag_id": last_run_info.get("dag_id"),
        "dag_run_id": last_run_info.get("dag_run_id"),
        "status": last_run_info.get("status"),
        "mode": last_run_info.get("mode"),
        "triggered_at": last_run_info.get("triggered_at"),
        "finished_at": last_run_info.get("finished_at"),
        "duration_sec": last_run_info.get("duration_sec"),
        "created_at": last_run_info.get("finished_at")
        or last_run_info.get("triggered_at"),
        "message": last_run_info.get("message"),
    }


def pipeline_entity_row(
    *,
    entity_config: dict,
    cartridge: str,
    dag_run: dict | None,
    last_job: dict | None,
    physical_bronze: dict | None,
    partial_reasons: set[str] | list[str] | tuple[str, ...] | None,
    silver_by_source: dict[str, list[dict]],
    gold_datasets: list[dict],
) -> dict:
    entity = entity_config.get("entity") or entity_config.get("name") or ""
    source = f"raw/{cartridge}/{entity}"

    last_run_info = None
    dag_status = None
    bronze_date, bronze_count = pipeline_bronze_date_count(dag_run, last_job)

    if dag_run:
        last_run_info, dag_status = pipeline_airflow_last_run_info(dag_run)
    elif last_job:
        last_run_info = pipeline_job_last_run_info(last_job)

    if (not bronze_date or bronze_count is None) and physical_bronze:
        bronze_date = bronze_date or physical_bronze.get("latest_date")
        if bronze_count is None:
            bronze_count = physical_bronze.get("record_count")
        if last_run_info is None and bronze_date:
            last_run_info = pipeline_bronze_last_run_info(
                bronze_date=bronze_date,
                bronze_count=bronze_count,
                mode=entity_config.get("mode"),
            )

    entity_partial = sorted(set(partial_reasons or ()))
    bronze_status = pipeline_bronze_status(
        dag_run=dag_run,
        dag_status=dag_status,
        bronze_date=bronze_date,
        bronze_count=bronze_count,
        has_partial_reasons=bool(entity_partial),
    )

    is_failed = (dag_run and dag_run["status"] == "failed") or (
        last_job and last_job.get("status") == "failed"
    )
    silver_nodes, gold_nodes = pipeline_silver_gold_nodes(
        source=source,
        silver_by_source=silver_by_source,
        gold_datasets=gold_datasets,
        failed=bool(is_failed),
        cartridge=cartridge,
    )

    return {
        "entity": entity,
        "cartridge": cartridge,
        "modes": entity_config.get("modes")
        or ([entity_config["mode"]] if entity_config.get("mode") else ["full"]),
        "watermark": entity_config.get("watermark_field") or "",
        "last_run": last_run_info,
        # Keep last_job for backward compat with pipeline.html polling logic
        "last_job": pipeline_legacy_last_job(last_run_info),
        "bronze": {
            "source": source,
            "latest_date": bronze_date,
            "record_count": bronze_count,
            "status": bronze_status,
            "empty": pipeline_is_zero_count(bronze_count),
        },
        "silver": silver_nodes,
        "gold": gold_nodes,
        "metadata": {
            "partial": bool(entity_partial),
            "pending": entity_partial,
            "stale": bool(entity_partial),
        },
    }


def pipeline_response_payload(
    rows: list[dict], partial_reasons: dict[str, set[str]]
) -> dict:
    order = {
        "running": 0,
        "error": 1,
        "partial": 2,
        "empty": 3,
        "stale": 4,
        "fresh": 5,
        "never": 6,
        "unknown": 7,
    }
    sorted_rows = sorted(rows, key=lambda row: order.get(row["bronze"]["status"], 5))
    pending_entities = sorted(partial_reasons)
    return {
        "pipeline": sorted_rows,
        "metadata": {
            "partial": bool(pending_entities),
            "pending_entities": pending_entities,
            "stale": bool(pending_entities),
        },
        "partial": bool(pending_entities),
        "pending_entities": pending_entities,
    }


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
