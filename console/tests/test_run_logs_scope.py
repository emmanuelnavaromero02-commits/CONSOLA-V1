from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domains.pipeline.run_logs import build_job_logs_payload


class _Pool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, sql, *args):
        self.calls.append(("execute", args))

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", args))
        return [{"entity": "OINV", "level": "info", "message": "ok", "detail": "{}", "ts": datetime(2026, 9, 25, tzinfo=timezone.utc)}]


class _Jobs:
    async def get_scoped(self, job_id, *, user):
        return {"args": {"cartridge_id": "sap_b1"}}


@pytest.mark.asyncio
async def test_run_logs_are_read_inside_the_callers_workspace_scope():
    pool = _Pool()

    async def get_pool():
        return pool

    user = {"id": 5, "tenant_id": "t-1", "active_workspace_id": "w-1"}
    payload = await build_job_logs_payload(job_id="job-1", limit=10, user=user, job_service=_Jobs(), get_db_pool=get_pool)
    assert [call[0] for call in pool.calls] == ["execute", "fetch"]
    assert pool.calls[0][1] == ("t-1", "w-1")
    assert pool.calls[1][1] == ("job-1", "sap_b1", 10)
    assert payload
