from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.domains.pipeline.run_state import normalize_airflow_state

RefreshDagRunStatus = Callable[[dict[str, Any], dict | None], Awaitable[dict[str, Any]]]


def is_pipeline_job_payload(job: dict | None) -> bool:
    if not isinstance(job, dict):
        return False
    result = job.get("result")
    return isinstance(result, dict) and result.get("source") == "pipeline_runs"


async def refresh_pipeline_job_payload(
    job: dict,
    user: dict | None,
    *,
    refresh_dag_run_status: RefreshDagRunStatus,
) -> dict:
    if not is_pipeline_job_payload(job):
        return job
    status = str(job.get("status") or "").lower()
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    args = job.get("args") if isinstance(job.get("args"), dict) else {}
    if status not in {"running", "queued"}:
        return job

    row = {
        "run_id": job.get("job_id"),
        "dag_id": args.get("dag_id") or result.get("dag_id"),
        "airflow_dag_run_id": (
            args.get("dag_run_id")
            or result.get("dag_run_id")
            or result.get("airflow_dag_run_id")
            or job.get("job_id")
        ),
        "status": result.get("pipeline_status") or status,
        "started_at": job.get("created_at"),
        "finished_at": job.get("finished_at"),
        "duration_seconds": None,
    }
    refreshed = await refresh_dag_run_status(row, user)
    pipeline_status = normalize_airflow_state(refreshed.get("status"))
    result["pipeline_status"] = pipeline_status
    job["result"] = result
    if pipeline_status in {"queued", "running", "unknown"}:
        job["status"] = "running"
        job["finished_at"] = None
    elif pipeline_status == "failed":
        job["status"] = "failed"
        job["finished_at"] = _iso_or_value(refreshed.get("finished_at"))
    else:
        job["status"] = "done"
        job["finished_at"] = _iso_or_value(refreshed.get("finished_at"))
    job["updated_at"] = job.get("finished_at") or job.get("updated_at")
    return job


async def refresh_pipeline_job_payloads(
    jobs: list[dict],
    user: dict | None,
    *,
    refresh_dag_run_status: RefreshDagRunStatus,
) -> list[dict]:
    refreshed: list[dict] = []
    for job in jobs:
        refreshed.append(
            await refresh_pipeline_job_payload(
                job,
                user,
                refresh_dag_run_status=refresh_dag_run_status,
            )
        )
    return refreshed


def _iso_or_value(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value
