from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services import job_service


def _user() -> dict:
    return {
        "id": 42,
        "email": "tenant-admin@example.test",
        "role": "tenant_admin",
        "active_tenant_id": "11111111-1111-1111-1111-111111111111",
        "active_workspace_id": "22222222-2222-2222-2222-222222222222",
        "allowed_cartridges": ["sap_successfactors"],
    }


def _pipeline_row(**overrides):
    row = {
        "run_id": "manual__2026-06-26T00:00:00+00:00",
        "dag_id": "sap_successfactors_extract_all",
        "cartridge_id": "sap_successfactors",
        "entity": "JobApplication",
        "airflow_dag_run_id": "manual__2026-06-26T00:00:00+00:00",
        "mode": "incremental",
        "status": "partial",
        "row_count": 12,
        "started_at": datetime(2026, 6, 26, 0, 0, tzinfo=timezone.utc),
        "finished_at": datetime(2026, 6, 26, 0, 3, tzinfo=timezone.utc),
        "error_message": None,
        "extra": {"reason": "invalid_select_field"},
    }
    row.update(overrides)
    return row


class _Pool:
    def __init__(self, *, pipeline_rows=None, job_rows=None):
        self.pipeline_rows = list(pipeline_rows or [])
        self.job_rows = list(job_rows or [])

    async def fetchval(self, query, *args):
        return args and args[0] in {"tenant_id", "workspace_id"}

    async def fetchrow(self, query, *args):
        if "FROM jobs" in query:
            return self.job_rows[0] if self.job_rows else None
        if "FROM pipeline_runs" in query:
            return self.pipeline_rows[0] if self.pipeline_rows else None
        return None

    async def fetch(self, query, *args):
        if "FROM jobs" in query:
            return list(self.job_rows)
        if "FROM pipeline_runs" in query:
            return list(self.pipeline_rows)
        return []


@pytest.fixture(autouse=True)
def _reset_job_service(monkeypatch):
    job_service._PIPELINE_COLUMN_EXISTS_CACHE.clear()
    monkeypatch.setattr(job_service, "_pool", None)
    yield
    job_service._PIPELINE_COLUMN_EXISTS_CACHE.clear()
    monkeypatch.setattr(job_service, "_pool", None)


@pytest.mark.anyio
async def test_get_scoped_falls_back_to_pipeline_runs(monkeypatch):
    pool = _Pool(pipeline_rows=[_pipeline_row()])
    monkeypatch.setattr(job_service, "_pool", pool)

    job = await job_service.get_scoped("manual__2026-06-26T00:00:00+00:00", user=_user())

    assert job["job_id"] == "manual__2026-06-26T00:00:00+00:00"
    assert job["tool"] == "airflow.pipeline_run"
    assert job["status"] == "done"
    assert job["args"]["entity"] == "JobApplication"
    assert job["result"]["pipeline_status"] == "partial"
    assert job["result"]["record_count"] == 12
    assert "invalid_select_field" in job["message"]


@pytest.mark.anyio
async def test_list_recent_includes_pipeline_runs(monkeypatch):
    pool = _Pool(
        pipeline_rows=[_pipeline_row(entity="CompetencyEntity", row_count=128)],
        job_rows=[
            {
                "job_id": "legacy-job",
                "tool": "legacy.extract",
                "args": {"cartridge_id": "sap_successfactors", "entity": "User"},
                "status": "done",
                "message": "ok",
                "result": {"record_count": 5},
                "error": None,
                "created_at": datetime(2026, 6, 25, 23, 0, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 6, 25, 23, 1, tzinfo=timezone.utc),
                "finished_at": datetime(2026, 6, 25, 23, 1, tzinfo=timezone.utc),
            }
        ],
    )
    monkeypatch.setattr(job_service, "_pool", pool)

    jobs = await job_service.list_recent(10, user=_user())

    assert [job["job_id"] for job in jobs] == [
        "manual__2026-06-26T00:00:00+00:00",
        "legacy-job",
    ]
    assert jobs[0]["tool"] == "airflow.pipeline_run"
    assert jobs[0]["result"]["record_count"] == 128
