from __future__ import annotations

import asyncio
import importlib
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi import HTTPException


REPO_ROOT = Path(__file__).resolve().parents[1]
CONSOLE_DIR = REPO_ROOT / "console"
SERVICE_PATH_MARKERS = (
    "/cartridges/",
    "/console",
    "/mcp-infra",
    "/refinement",
    "/vault",
    "/workspace",
)


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


@pytest.fixture
def console_main(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-internal-key-aaaaaaaaaaaaaaaaaaaaaaaa")
    monkeypatch.setenv("INTERNAL_API_KEY_CONSOLE_TO_CONSOLE", os.environ["INTERNAL_API_KEY"])
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-key-bbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", "test-security-context-key-cccccccccccccccccccc")
    saved_path = list(sys.path)
    sys.path[:] = [
        p for p in sys.path
        if not any(marker in p for marker in SERVICE_PATH_MARKERS)
    ]
    sys.path.insert(0, str(CONSOLE_DIR))
    _purge_app_modules()
    main = importlib.import_module("app.main")
    yield main
    _purge_app_modules()
    sys.path[:] = saved_path


class FakePool:
    def __init__(self, *, fetch_rows=None, fetchrow_result=None):
        self.fetch_rows = fetch_rows or []
        self.fetchrow_result = fetchrow_result

    async def fetch(self, *_args, **_kwargs):
        return self.fetch_rows

    async def fetchrow(self, *_args, **_kwargs):
        return self.fetchrow_result

    async def execute(self, *_args, **_kwargs):
        return None


class FakeMcpRegistry:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def invoke(self, _server, tool, args, user=None):
        self.calls.append((tool, args, user))
        response = self.responses.get(tool)
        if callable(response):
            return response(args)
        return response or {}


def _user() -> dict:
    return {
        "id": 1,
        "email": "demo@example.com",
        "role": "admin",
        "workspace_role": "admin",
        "tenant_id": "tenant-demo",
        "workspace_id": "workspace-demo",
        "permissions": ["*"],
        "allowed_cartridges": ["sap_successfactors"],
    }


async def _resolve_cartridge(_user, cartridge, fallback="sap_successfactors"):
    resolved = cartridge or fallback
    return resolved, [resolved]


async def _scope(_user, _start_index):
    return "", []


async def _datasets(*_args, **_kwargs):
    return {"datasets": []}


async def _jobs(*_args, **_kwargs):
    return []


def _patch_pipeline_common(monkeypatch, main, *, fetch_rows=None):
    async def get_cartridge(_cartridge):
        return {
            "entities": [
                {"id": "EmpJob", "mode": "full", "watermark_field": "lastModifiedDateTime"},
            ],
        }

    async def get_db_pool():
        return FakePool(fetch_rows=fetch_rows or [])

    monkeypatch.setattr(main, "_resolve_scoped_operation_cartridge", _resolve_cartridge)
    monkeypatch.setattr(main.cartridge_service, "get_cartridge", get_cartridge)
    monkeypatch.setattr(main, "_get_db_pool", get_db_pool)
    monkeypatch.setattr(main, "_pipeline_runs_scope_predicate", _scope)
    monkeypatch.setattr(main, "_refinement_invoke", _datasets)
    monkeypatch.setattr(main.job_service, "list_recent", _jobs)


def _patch_log_common(monkeypatch, main, *, row):
    async def metadata(*_args, **_kwargs):
        return {"entity": "EmpJob", "dag_id": "sap_successfactors_extract"}

    async def get_db_pool():
        return FakePool(fetchrow_result=row)

    async def refresh(row, _user=None):
        return row

    monkeypatch.setattr(main, "_require_cartridge_visible", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "_pipeline_extract_metadata", metadata)
    monkeypatch.setattr(main, "_get_db_pool", get_db_pool)
    monkeypatch.setattr(main, "_pipeline_runs_scope_predicate", _scope)
    monkeypatch.setattr(main, "_refresh_dag_run_status", refresh)


@pytest.mark.asyncio
async def test_api_pipeline_snapshot_timeout_returns_partial_shape_without_false_zero(console_main, monkeypatch):
    main = console_main
    _patch_pipeline_common(monkeypatch, main)
    monkeypatch.setattr(main, "PIPELINE_BRONZE_SNAPSHOT_TIMEOUT_SEC", 0.01)

    async def slow_snapshot(*_args, **_kwargs):
        await asyncio.sleep(1)
        return {"latest_date": "2026-06-10", "record_count": 123}

    monkeypatch.setattr(main, "_bronze_physical_snapshot", slow_snapshot)

    started = time.perf_counter()
    payload = await asyncio.wait_for(
        main.api_pipeline("sap_successfactors", user=_user()),
        timeout=0.5,
    )
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5
    assert set(payload) >= {"pipeline", "metadata", "partial", "pending_entities"}
    assert payload["partial"] is True
    assert payload["metadata"]["partial"] is True
    assert payload["pending_entities"] == ["EmpJob"]

    row = payload["pipeline"][0]
    assert set(row) >= {"entity", "cartridge", "last_run", "last_job", "bronze", "silver", "gold"}
    assert row["bronze"]["record_count"] is None
    assert row["bronze"]["status"] == "unknown"
    assert row["metadata"] == {
        "partial": True,
        "pending": ["bronze_snapshot"],
        "stale": True,
    }


@pytest.mark.asyncio
async def test_api_pipeline_airflow_refresh_timeout_does_not_block_or_mark_never(console_main, monkeypatch):
    main = console_main
    _patch_pipeline_common(
        monkeypatch,
        main,
        fetch_rows=[
            {
                "run_id": "run-1",
                "dag_id": "sap_successfactors_extract",
                "entity": "EmpJob",
                "airflow_dag_run_id": "manual__demo",
                "status": "running",
                "mode": "full",
                "started_at": None,
                "finished_at": None,
                "record_count": None,
                "bytes_written": None,
                "storage_uri": None,
                "duration_seconds": None,
                "watermark_updated_to": None,
                "error_message": None,
                "extra": {},
            },
        ],
    )
    monkeypatch.setattr(main, "PIPELINE_DAG_STATUS_TIMEOUT_SEC", 0.01)
    monkeypatch.setattr(main, "PIPELINE_BRONZE_SNAPSHOT_TIMEOUT_SEC", 0.01)

    async def slow_refresh(*_args, **_kwargs):
        await asyncio.sleep(1)
        return {}

    async def no_snapshot(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(main, "_refresh_dag_run_status", slow_refresh)
    monkeypatch.setattr(main, "_bronze_physical_snapshot", no_snapshot)

    payload = await asyncio.wait_for(
        main.api_pipeline("sap_successfactors", user=_user()),
        timeout=0.5,
    )

    row = payload["pipeline"][0]
    assert row["bronze"]["status"] == "running"
    assert row["bronze"]["record_count"] is None
    assert payload["partial"] is True
    assert row["metadata"]["pending"] == ["airflow_status_refresh"]


def test_airflow_log_task_resolution_prefers_real_sap_tasks(console_main):
    main = console_main

    assert main._airflow_log_task_ids(
        "sap_successfactors_extract",
        [{"task_id": "trigger_extract"}],
    ) == ["trigger_extract"]
    assert main._airflow_log_task_ids(
        "sap_successfactors_extract",
        [{"task_id": "trigger_extract"}],
    ) != ["extract"]
    assert main._airflow_log_task_ids(
        "sap_successfactors_extract_all",
        [{"task_id": "trigger_extract_all"}],
    ) == ["trigger_extract_all"]


@pytest.mark.asyncio
async def test_pipeline_run_logs_fetches_trigger_extract_and_not_extract(console_main, monkeypatch):
    main = console_main
    row = {
        "run_id": "run-1",
        "dag_id": "sap_successfactors_extract",
        "airflow_dag_run_id": "manual__demo",
        "status": "success",
        "mode": "full",
        "started_at": None,
        "finished_at": None,
        "duration_seconds": None,
        "error_message": None,
    }
    registry = FakeMcpRegistry({
        "airflow_list_task_instances": {"tasks": [{"task_id": "trigger_extract"}]},
        "airflow_get_task_logs": {"logs": "real airflow logs"},
    })

    _patch_log_common(monkeypatch, main, row=row)
    monkeypatch.setattr(main, "mcp_registry", registry)

    payload = await main.api_pipeline_run_logs(
        "sap_successfactors",
        "EmpJob",
        "manual__demo",
        user=_user(),
    )

    assert payload["available"] is True
    assert payload["logs"][0]["task_id"] == "trigger_extract"
    assert payload["logs"][0]["logs"] == "real airflow logs"
    assert [args["task_id"] for tool, args, _ in registry.calls if tool == "airflow_get_task_logs"] == [
        "trigger_extract",
    ]


@pytest.mark.asyncio
async def test_pipeline_run_logs_returns_clear_error_when_task_or_logs_missing(console_main, monkeypatch):
    main = console_main
    row = {
        "run_id": "run-1",
        "dag_id": "sap_successfactors_extract",
        "airflow_dag_run_id": "manual__demo",
        "status": "success",
        "mode": "full",
        "started_at": None,
        "finished_at": None,
        "duration_seconds": None,
        "error_message": None,
    }
    registry = FakeMcpRegistry({
        "airflow_list_task_instances": {"tasks": [{"task_id": "other_task"}]},
    })

    _patch_log_common(monkeypatch, main, row=row)
    monkeypatch.setattr(main, "mcp_registry", registry)

    payload = await main.api_pipeline_run_logs(
        "sap_successfactors",
        "EmpJob",
        "manual__demo",
        user=_user(),
    )

    assert payload["available"] is False
    assert "No Airflow log task found" in payload["error"]
    assert payload["attempted"] == {
        "dag_id": "sap_successfactors_extract",
        "dag_run_id": "manual__demo",
        "task_ids": [],
        "available_task_ids": ["other_task"],
    }
    assert not [call for call in registry.calls if call[0] == "airflow_get_task_logs"]


@pytest.mark.asyncio
async def test_pipeline_run_logs_returns_clear_error_when_logs_missing(console_main, monkeypatch):
    main = console_main
    row = {
        "run_id": "run-1",
        "dag_id": "sap_successfactors_extract",
        "airflow_dag_run_id": "manual__demo",
        "status": "success",
        "mode": "full",
        "started_at": None,
        "finished_at": None,
        "duration_seconds": None,
        "error_message": None,
    }
    registry = FakeMcpRegistry({
        "airflow_list_task_instances": {"tasks": [{"task_id": "trigger_extract"}]},
        "airflow_get_task_logs": {"error": "log not found"},
    })

    _patch_log_common(monkeypatch, main, row=row)
    monkeypatch.setattr(main, "mcp_registry", registry)

    payload = await main.api_pipeline_run_logs(
        "sap_successfactors",
        "EmpJob",
        "manual__demo",
        user=_user(),
    )

    assert payload["available"] is False
    assert "No Airflow logs found" in payload["error"]
    assert payload["attempted"]["task_ids"] == ["trigger_extract"]
    assert payload["logs"] == [{"task_id": "trigger_extract", "available": False, "error": "log not found"}]


@pytest.mark.asyncio
async def test_pipeline_run_logs_returns_attempted_context_when_run_missing(console_main, monkeypatch):
    main = console_main

    _patch_log_common(monkeypatch, main, row=None)

    with pytest.raises(HTTPException) as exc:
        await main.api_pipeline_run_logs(
            "sap_successfactors",
            "EmpJob",
            "missing-run",
            user=_user(),
        )

    assert exc.value.status_code == 404
    assert exc.value.detail["attempted"] == {
        "dag_id": "sap_successfactors_extract",
        "dag_run_id": "missing-run",
        "task_ids": [],
    }


@pytest.mark.asyncio
async def test_studio_ops_get_entity_logs_uses_trigger_extract(console_main, monkeypatch):
    main = console_main
    row = {
        "dag_id": "sap_successfactors_extract",
        "airflow_dag_run_id": "manual__demo",
        "status": "success",
        "mode": "full",
        "started_at": "2026-06-11T01:00:00Z",
        "finished_at": "2026-06-11T01:01:00Z",
        "record_count": 3,
        "error_message": None,
        "extra": {},
    }
    registry = FakeMcpRegistry({
        "airflow_list_task_instances": {"tasks": [{"task_id": "trigger_extract"}]},
        "airflow_get_task_logs": {"logs": "studio ops logs"},
    })

    async def get_db_pool():
        return FakePool(fetchrow_result=row)

    monkeypatch.setattr(main, "_is_internal_service_actor", lambda _user: True)
    monkeypatch.setattr(main, "_require_cartridge_visible", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "_get_db_pool", get_db_pool)
    monkeypatch.setattr(main, "_pipeline_runs_scope_predicate", _scope)
    monkeypatch.setattr(main, "mcp_registry", registry)

    payload = await main.studio_ops_invoke(
        {"tool": "get_entity_logs", "args": {"cartridge_id": "sap_successfactors", "entity": "EmpJob"}},
        user=_user(),
    )

    assert payload["airflow_logs"] == "studio ops logs"
    assert payload["airflow_log_error"] is None
    assert payload["attempted"]["task_ids"] == ["trigger_extract"]
    assert [args["task_id"] for tool, args, _ in registry.calls if tool == "airflow_get_task_logs"] == [
        "trigger_extract",
    ]
