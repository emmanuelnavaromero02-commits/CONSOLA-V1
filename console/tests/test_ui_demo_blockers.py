"""Regression tests for the two UI demo-blocker fixes (branch fix/ui-demo-blockers).

FIX-1  /api/pipeline must not hang and must not surface a fabricated 0 when the
       physical bronze probe can't finish in time — it returns a partial result.
FIX-2  Airflow "Ver logs" must discover the real task id (e.g. ``trigger_extract``)
       instead of the hardcoded ``extract`` that never exists.

These reuse the in-process ``console_main`` fixture defined in
``test_pipeline_extract.py`` (same directory), which imports ``app.main`` with
service stubs and no real DB / MinIO / Airflow.
"""
from __future__ import annotations

import asyncio
import time

import pytest

# Reuse the heavy fixture + anyio backend from the sibling test module so we get
# an importable ``app.main`` with all external services stubbed out.
from test_pipeline_extract import anyio_backend, console_main  # noqa: F401


# --------------------------------------------------------------------------- #
# FIX-1 — /api/pipeline performance / partial-instead-of-false-zero
# --------------------------------------------------------------------------- #

async def _no_datasets(tool, args, timeout=15, user=None):
    return {"datasets": []}


@pytest.mark.anyio
async def test_api_pipeline_budget_returns_partial_not_false_zero(console_main, monkeypatch):
    """When the physical bronze probe exceeds the budget, the endpoint returns
    promptly with meta.partial=True and record_count=None — never a fake 0,
    never a hang."""
    async def get_cartridge(cartridge):
        return {"entities": [{"id": "EmpCompensation", "mode": "full", "description": "x"}]}

    async def slow_snapshot(cartridge, entity):
        await asyncio.sleep(2.0)  # far longer than the budget below
        return {"latest_date": "2026-06-09", "record_count": 999}

    monkeypatch.setattr(console_main.cartridge_service, "get_cartridge", get_cartridge)
    monkeypatch.setattr(console_main, "_bronze_physical_snapshot", slow_snapshot)
    monkeypatch.setattr(console_main, "_refinement_invoke", _no_datasets, raising=False)
    monkeypatch.setattr(console_main, "_PIPELINE_PHYSICAL_BUDGET_S", 0.3, raising=False)

    t0 = time.monotonic()
    result = await console_main.api_pipeline("sap_successfactors")
    elapsed = time.monotonic() - t0

    # 1) did not block on the 2s snapshot
    assert elapsed < 1.5
    # 2) shape stays compatible with the frontend (reads `pipeline`)
    assert "pipeline" in result and isinstance(result["pipeline"], list)
    row = result["pipeline"][0]
    assert row["entity"] == "EmpCompensation"
    # 3) NOT a fabricated 0 — unknown count is None
    assert row["bronze"]["record_count"] is None
    # 4) explicit partial signal so the UI shows "calculando…" not 0
    assert result["meta"]["partial"] is True
    assert result["meta"]["status"] == "partial"
    assert "EmpCompensation" in result["meta"]["pending_entities"]


@pytest.mark.anyio
async def test_api_pipeline_fast_path_fills_count_and_meta_ok(console_main, monkeypatch):
    """When probes resolve inside the budget, counts are filled and meta=ok."""
    async def get_cartridge(cartridge):
        return {"entities": [{"id": "EmpEmployment", "mode": "full", "description": "x"}]}

    async def fast_snapshot(cartridge, entity):
        return {"latest_date": "2026-06-09", "record_count": 42}

    monkeypatch.setattr(console_main.cartridge_service, "get_cartridge", get_cartridge)
    monkeypatch.setattr(console_main, "_bronze_physical_snapshot", fast_snapshot)
    monkeypatch.setattr(console_main, "_refinement_invoke", _no_datasets, raising=False)

    result = await console_main.api_pipeline("sap_successfactors")
    row = result["pipeline"][0]

    assert row["entity"] == "EmpEmployment"
    assert row["bronze"]["record_count"] == 42
    assert row["bronze"]["latest_date"] == "2026-06-09"
    assert result["meta"]["partial"] is False
    assert result["meta"]["status"] == "ok"
    assert result["meta"]["pending_entities"] == []


@pytest.mark.anyio
async def test_api_pipeline_concurrent_probes_complete_for_many_entities(console_main, monkeypatch):
    """Many entities needing a probe still finish well under the per-probe time
    because probes run concurrently (bounded), not serially."""
    entities = [{"id": f"E{i}", "mode": "full", "description": ""} for i in range(12)]

    async def get_cartridge(cartridge):
        return {"entities": entities}

    async def snapshot(cartridge, entity):
        await asyncio.sleep(0.2)  # 12 serial => 2.4s; concurrent => ~0.2-0.6s
        return {"latest_date": "2026-06-09", "record_count": 1}

    monkeypatch.setattr(console_main.cartridge_service, "get_cartridge", get_cartridge)
    monkeypatch.setattr(console_main, "_bronze_physical_snapshot", snapshot)
    monkeypatch.setattr(console_main, "_refinement_invoke", _no_datasets, raising=False)
    monkeypatch.setattr(console_main, "_PIPELINE_PHYSICAL_BUDGET_S", 5.0, raising=False)

    t0 = time.monotonic()
    result = await console_main.api_pipeline("sap_successfactors")
    elapsed = time.monotonic() - t0

    assert elapsed < 1.5  # would be ~2.4s if serial
    assert len(result["pipeline"]) == 12
    assert all(r["bronze"]["record_count"] == 1 for r in result["pipeline"])
    assert result["meta"]["partial"] is False


@pytest.mark.anyio
async def test_bronze_physical_snapshot_enumerates_off_event_loop(console_main, monkeypatch):
    """The blocking MinIO list_objects is run via a thread and still yields the
    correct latest_date / record_count."""
    class _Obj:
        def __init__(self, name):
            self.object_name = name

    class _FakeMinio:
        def list_objects(self, bucket, prefix, recursive):
            assert recursive is True
            return [
                _Obj("raw/sap_successfactors/EmpJob/load_date=2026-06-08/d.parquet"),
                _Obj("raw/sap_successfactors/EmpJob/load_date=2026-06-09/d.parquet"),
            ]

    async def count(source, latest_date, user):
        assert latest_date == "2026-06-09"
        return 7

    monkeypatch.setattr(console_main, "_minio_client", lambda: _FakeMinio())
    monkeypatch.setattr(console_main, "_explorer_path_allowed", lambda *a, **k: True)
    monkeypatch.setattr(console_main, "_count_bronze_parquet_rows", count)

    snap = await console_main._bronze_physical_snapshot("sap_successfactors", "EmpJob", None)

    assert snap["latest_date"] == "2026-06-09"
    assert snap["record_count"] == 7


# --------------------------------------------------------------------------- #
# FIX-2 — Airflow "Ver logs" task-id discovery
# --------------------------------------------------------------------------- #

@pytest.mark.anyio
async def test_resolve_airflow_logs_uses_real_task_not_hardcoded_extract(console_main, monkeypatch):
    calls = []

    async def invoke(server, tool, args, **_kw):
        calls.append((tool, dict(args)))
        if tool == "airflow_list_task_instances":
            return {"tasks": [{"task_id": "trigger_extract", "state": "success", "duration": 2.0}]}
        if tool == "airflow_get_task_logs":
            return {"logs": "real log line", "dag_id": args["dag_id"], "task_id": args["task_id"]}
        return {}

    monkeypatch.setattr(console_main.mcp_registry, "invoke", invoke)

    result = await console_main._resolve_airflow_entity_logs(
        "sap_successfactors_extract", "manual__run1", None,
    )

    assert result["available"] is True
    assert "real log line" in result["logs"]
    assert result["tasks_tried"] == ["trigger_extract"]
    log_calls = [a for (t, a) in calls if t == "airflow_get_task_logs"]
    assert log_calls, "expected at least one airflow_get_task_logs call"
    # The real task id is used; the broken hardcoded "extract" is never requested.
    assert all(a["task_id"] == "trigger_extract" for a in log_calls)
    assert all(a["task_id"] != "extract" for a in log_calls)


@pytest.mark.anyio
async def test_resolve_airflow_logs_extract_all_dag(console_main, monkeypatch):
    async def invoke(server, tool, args, **_kw):
        if tool == "airflow_list_task_instances":
            assert args["dag_id"] == "sap_successfactors_extract_all"
            return {"tasks": [{"task_id": "trigger_extract", "state": "success"}]}
        if tool == "airflow_get_task_logs":
            return {"logs": "all-entities log"}
        return {}

    monkeypatch.setattr(console_main.mcp_registry, "invoke", invoke)

    result = await console_main._resolve_airflow_entity_logs(
        "sap_successfactors_extract_all", "manual__all", None,
    )
    assert result["available"] is True
    assert "all-entities log" in result["logs"]


@pytest.mark.anyio
async def test_resolve_airflow_logs_missing_run_id_returns_clear_message(console_main):
    result = await console_main._resolve_airflow_entity_logs(
        "sap_successfactors_extract", None, None,
    )
    assert result["available"] is False
    assert result["logs"] == ""
    assert "run de Airflow" in result["detail"]
    assert result["dag_id"] == "sap_successfactors_extract"
    assert result["dag_run_id"] is None


@pytest.mark.anyio
async def test_resolve_airflow_logs_no_task_instances(console_main, monkeypatch):
    async def invoke(server, tool, args, **_kw):
        if tool == "airflow_list_task_instances":
            return {"tasks": []}
        return {}

    monkeypatch.setattr(console_main.mcp_registry, "invoke", invoke)

    result = await console_main._resolve_airflow_entity_logs(
        "sap_successfactors_extract_all", "manual__run2", None,
    )
    assert result["available"] is False
    assert "task instances" in result["detail"]
    assert result["dag_run_id"] == "manual__run2"


@pytest.mark.anyio
async def test_resolve_airflow_logs_failed_task_first(console_main, monkeypatch):
    order = []

    async def invoke(server, tool, args, **_kw):
        if tool == "airflow_list_task_instances":
            return {"tasks": [
                {"task_id": "noop", "state": "success"},
                {"task_id": "trigger_extract", "state": "failed"},
            ]}
        if tool == "airflow_get_task_logs":
            order.append(args["task_id"])
            return {"logs": f"log for {args['task_id']}"}
        return {}

    monkeypatch.setattr(console_main.mcp_registry, "invoke", invoke)

    result = await console_main._resolve_airflow_entity_logs(
        "sap_successfactors_extract", "r", None,
    )
    assert result["available"] is True
    assert order[0] == "trigger_extract"  # failed task fetched first
    assert "log for trigger_extract" in result["logs"]
    assert "log for noop" in result["logs"]


@pytest.mark.anyio
async def test_resolve_airflow_logs_tasks_return_no_logs(console_main, monkeypatch):
    async def invoke(server, tool, args, **_kw):
        if tool == "airflow_list_task_instances":
            return {"tasks": [{"task_id": "trigger_extract", "state": "success"}]}
        if tool == "airflow_get_task_logs":
            return {"logs": ""}  # task exists but no retrievable log
        return {}

    monkeypatch.setattr(console_main.mcp_registry, "invoke", invoke)

    result = await console_main._resolve_airflow_entity_logs(
        "sap_successfactors_extract", "r", None,
    )
    assert result["available"] is False
    assert result["tasks_tried"] == ["trigger_extract"]
    assert "no devolvieron logs" in result["detail"]
