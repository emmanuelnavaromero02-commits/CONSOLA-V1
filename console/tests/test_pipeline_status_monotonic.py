from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domains.pipeline.recording import (
    record_dag_pipeline_trigger,
    refresh_dag_run_status,
)
from app.domains.pipeline.run_state import duration_seconds, parse_iso_datetime
from app.domains.pipeline.status_transitions import (
    advance_pipeline_status,
    pipeline_status_accepts,
)


TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "22222222-2222-2222-2222-222222222222"


def test_terminal_before_airflow_ack_never_reopens_the_run() -> None:
    assert advance_pipeline_status("success", "queued") == "success"
    assert advance_pipeline_status("running", "noop") == "noop"
    assert advance_pipeline_status("partial", "running") == "partial"
    assert advance_pipeline_status("failed", "success") == "failed"
    assert not pipeline_status_accepts("failed", "running")


def test_airflow_success_can_be_refined_by_blocked_product_children() -> None:
    assert advance_pipeline_status("success", "partial") == "partial"
    assert advance_pipeline_status("partial", "blocked") == "blocked"
    assert advance_pipeline_status("blocked", "failed") == "failed"
    assert advance_pipeline_status("partial", "success") == "partial"


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_exc):
        return False


class _RecordingConnection:
    def __init__(self, *, durable_row=None, update_row=None):
        self.durable_row = durable_row
        self.update_row = update_row
        self.executed: list[tuple[str, tuple]] = []
        self.fetches: list[tuple[str, tuple]] = []

    def transaction(self):
        return _AsyncContext(self)

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "OK"

    async def fetchrow(self, sql, *args):
        self.fetches.append((sql, args))
        if "UPDATE pipeline_runs" in sql:
            return self.update_row
        if "SELECT * FROM pipeline_runs" in sql:
            return self.durable_row
        return None


class _RecordingPool:
    def __init__(self, conn: _RecordingConnection):
        self.conn = conn
        self.executed: list[tuple[str, tuple]] = []

    def acquire(self):
        return _AsyncContext(self.conn)

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "OK"


@pytest.mark.anyio
async def test_trigger_recording_uses_atomic_monotonic_upsert() -> None:
    conn = _RecordingConnection()
    pool = _RecordingPool(conn)

    async def table_has_column(_table, _column, **_kwargs):
        return True

    await record_dag_pipeline_trigger(
        cartridge="sap_successfactors",
        entity="__extract_all__",
        dag_id="sap_successfactors_extract_all",
        dag_run_id="manual__fast-terminal",
        mode="incremental",
        status="queued",
        conf={"target": "all"},
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        get_db_pool=lambda: _async_value(pool),
        table_has_column=table_has_column,
        normalize_airflow_state=lambda value: str(value),
    )

    upsert = next(
        sql for sql, _args in conn.executed if "INSERT INTO pipeline_runs" in sql
    )
    assert "CASE WHEN" in upsert
    assert "pipeline_runs.status" in upsert
    assert "EXCLUDED.status" in upsert
    assert "status = EXCLUDED.status" not in upsert


async def _async_value(value):
    return value


def _row(status: str = "running") -> dict:
    return {
        "run_id": "manual__race",
        "dag_id": "sap_successfactors_extract_all",
        "airflow_dag_run_id": "manual__race",
        "entity": "__extract_all__",
        "status": status,
        "started_at": datetime(2026, 8, 8, tzinfo=timezone.utc),
        "finished_at": None,
        "duration_seconds": None,
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "extra": {},
    }


@pytest.mark.anyio
async def test_airflow_refresh_losing_cas_returns_the_winning_terminal_row() -> None:
    durable = {**_row("partial"), "extra": {"summary": {"blocked": 1}}}
    conn = _RecordingConnection(durable_row=durable, update_row=None)
    pool = _RecordingPool(conn)

    async def invoke(*_args, **_kwargs):
        return {
            "state": "success",
            "start_date": "2026-08-08T00:00:00Z",
            "end_date": "2026-08-08T00:01:00Z",
        }

    result = await refresh_dag_run_status(
        _row(),
        {"tenant_id": TENANT, "workspace_id": WORKSPACE},
        mcp_invoke=invoke,
        get_db_pool=lambda: _async_value(pool),
        build_security_context=lambda user: user or {},
        normalize_airflow_state=lambda value: str(value or "unknown").lower(),
        parse_iso_datetime=parse_iso_datetime,
        duration_seconds=duration_seconds,
        logger_debug=lambda *_args, **_kwargs: None,
    )

    assert result["status"] == "partial"
    update_sql = next(
        sql for sql, _args in conn.fetches if "UPDATE pipeline_runs" in sql
    )
    assert "LOWER(COALESCE(status, 'unknown'))" in update_sql
    assert "tenant_id=$8::uuid AND workspace_id=$9::uuid" in update_sql
    assert any("SELECT * FROM pipeline_runs" in sql for sql, _args in conn.fetches)


@pytest.mark.anyio
async def test_airflow_404_retains_durable_row_for_explicit_reconciliation() -> None:
    pool_requested = False

    async def invoke(*_args, **_kwargs):
        return {"error": "DAG run not found", "status_code": 404}

    async def get_pool():
        nonlocal pool_requested
        pool_requested = True
        raise AssertionError("404 observation must not write pipeline_runs")

    current = _row()
    result = await refresh_dag_run_status(
        current,
        {"tenant_id": TENANT, "workspace_id": WORKSPACE},
        mcp_invoke=invoke,
        get_db_pool=get_pool,
        build_security_context=lambda user: user or {},
        normalize_airflow_state=lambda value: str(value or "unknown").lower(),
        parse_iso_datetime=parse_iso_datetime,
        duration_seconds=duration_seconds,
        logger_debug=lambda *_args, **_kwargs: None,
    )

    assert result is current
    assert result["status"] == "running"
    assert pool_requested is False
