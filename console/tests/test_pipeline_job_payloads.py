from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domains.pipeline.job_payloads import (
    is_pipeline_job_payload,
    refresh_pipeline_job_payload,
    refresh_pipeline_job_payloads,
)


def _pipeline_job(status: str = "running") -> dict:
    return {
        "job_id": "job-1",
        "status": status,
        "created_at": "2026-07-03T00:00:00Z",
        "updated_at": "2026-07-03T00:00:00Z",
        "args": {"dag_id": "sap_successfactors_extract"},
        "result": {"source": "pipeline_runs", "pipeline_status": status},
    }


def test_is_pipeline_job_payload_requires_pipeline_source():
    assert is_pipeline_job_payload(_pipeline_job())
    assert not is_pipeline_job_payload({"result": {"source": "other"}})
    assert not is_pipeline_job_payload(None)


@pytest.mark.asyncio
async def test_refresh_pipeline_job_payload_keeps_running_when_airflow_running():
    async def refresh(row, user):
        assert row["airflow_dag_run_id"] == "job-1"
        assert user == {"id": "u1"}
        return {"status": "running"}

    job = await refresh_pipeline_job_payload(
        _pipeline_job(),
        {"id": "u1"},
        refresh_dag_run_status=refresh,
    )

    assert job["status"] == "running"
    assert job["finished_at"] is None
    assert job["result"]["pipeline_status"] == "running"


@pytest.mark.asyncio
async def test_refresh_pipeline_job_payload_marks_failed_or_done():
    finished_at = datetime(2026, 7, 3, 1, 2, 3, tzinfo=timezone.utc)

    async def failed(_row, _user):
        return {"status": "failed", "finished_at": finished_at}

    failed_job = await refresh_pipeline_job_payload(
        _pipeline_job(),
        None,
        refresh_dag_run_status=failed,
    )

    assert failed_job["status"] == "failed"
    assert failed_job["finished_at"] == "2026-07-03T01:02:03+00:00"
    assert failed_job["updated_at"] == "2026-07-03T01:02:03+00:00"

    async def success(_row, _user):
        return {"status": "success", "finished_at": "done-at"}

    done_jobs = await refresh_pipeline_job_payloads(
        [_pipeline_job()],
        None,
        refresh_dag_run_status=success,
    )

    assert done_jobs[0]["status"] == "done"
    assert done_jobs[0]["finished_at"] == "done-at"
