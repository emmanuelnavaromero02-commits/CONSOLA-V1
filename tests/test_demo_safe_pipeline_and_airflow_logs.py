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


class FakeTimestamp:
    def isoformat(self):
        return "2026-06-11T01:02:03+00:00"


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


@pytest.mark.asyncio
async def test_api_pipeline_marks_zero_row_success_as_empty(console_main, monkeypatch):
    main = console_main
    _patch_pipeline_common(
        monkeypatch,
        main,
        fetch_rows=[
            {
                "run_id": "run-zero",
                "dag_id": "sap_successfactors_extract",
                "entity": "EmpJob",
                "airflow_dag_run_id": "manual__zero",
                "status": "success",
                "mode": "full",
                "started_at": "2026-06-11T01:00:00Z",
                "finished_at": "2026-06-11T01:01:00Z",
                "record_count": 0,
                "bytes_written": 128,
                "storage_uri": "s3://lakehouse/raw/sap_successfactors/EmpJob/data.parquet",
                "duration_seconds": 60,
                "watermark_updated_to": None,
                "error_message": None,
                "extra": {"empty_result": True, "result_status": "success"},
            },
        ],
    )

    async def refresh(row, _user=None):
        return row

    async def no_snapshot(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(main, "_refresh_dag_run_status", refresh)
    monkeypatch.setattr(main, "_bronze_physical_snapshot", no_snapshot)

    payload = await main.api_pipeline("sap_successfactors", user=_user())
    row = payload["pipeline"][0]

    assert row["bronze"]["status"] == "empty"
    assert row["bronze"]["empty"] is True
    assert row["last_run"]["empty_result"] is True
    assert row["last_run"]["result_status"] == "success"


@pytest.mark.asyncio
async def test_api_pipeline_surfaces_partial_downstream_refresh(console_main, monkeypatch):
    main = console_main
    _patch_pipeline_common(
        monkeypatch,
        main,
        fetch_rows=[
            {
                "run_id": "run-partial",
                "dag_id": "sap_successfactors_extract",
                "entity": "EmpJob",
                "airflow_dag_run_id": "manual__partial",
                "status": "partial",
                "mode": "full",
                "started_at": "2026-06-11T01:00:00Z",
                "finished_at": "2026-06-11T01:01:00Z",
                "record_count": 10,
                "bytes_written": 256,
                "storage_uri": "s3://lakehouse/raw/sap_successfactors/EmpJob/data.parquet",
                "duration_seconds": 60,
                "watermark_updated_to": None,
                "error_message": None,
                "extra": {
                    "result_status": "success",
                    "silver_refresh": {"status": "failed", "error": "Dataset no materializado"},
                },
            },
        ],
    )

    async def refresh(row, _user=None):
        return row

    async def no_snapshot(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(main, "_refresh_dag_run_status", refresh)
    monkeypatch.setattr(main, "_bronze_physical_snapshot", no_snapshot)

    payload = await main.api_pipeline("sap_successfactors", user=_user())
    row = payload["pipeline"][0]

    assert row["bronze"]["status"] == "partial"
    assert row["bronze"]["empty"] is False
    assert row["last_run"]["status"] == "partial"
    assert row["last_run"]["silver_refresh_status"] == "failed"
    assert row["last_run"]["extra"]["silver_refresh"]["error"] == "Dataset no materializado"


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


@pytest.mark.asyncio
async def test_api_job_logs_uses_scoped_job_cartridge(console_main, monkeypatch):
    main = console_main

    class RecordingPool:
        def __init__(self):
            self.calls = []

        async def fetch(self, query, *params):
            self.calls.append((query, params))
            return [
                {
                    "entity": "EmpJob",
                    "level": "INFO",
                    "message": "trigger_extract completed",
                    "detail": '{"task_id":"trigger_extract"}',
                    "ts": FakeTimestamp(),
                }
            ]

    pool = RecordingPool()

    async def get_db_pool():
        return pool

    async def get_scoped(job_id, user=None):
        assert job_id == "job-sf"
        assert user == _user()
        return {"args": {"cartridge": "sap_successfactors"}}

    monkeypatch.setattr(main, "_get_db_pool", get_db_pool)
    monkeypatch.setattr(main.job_service, "get_scoped", get_scoped)

    payload = await main.api_job_logs("job-sf", limit=50, user=_user())

    assert payload["logs"] == [
        {
            "ts": "2026-06-11T01:02:03+00:00",
            "entity": "EmpJob",
            "level": "INFO",
            "message": "trigger_extract completed",
            "detail": {"task_id": "trigger_extract"},
        }
    ]
    query, params = pool.calls[0]
    assert "cartridge='replicon'" not in query
    assert "cartridge=$2" in query
    assert params == ("job-sf", "sap_successfactors", 50)


@pytest.mark.asyncio
async def test_api_job_logs_fails_closed_when_job_cartridge_missing(console_main, monkeypatch):
    main = console_main

    async def get_scoped(_job_id, user=None):
        return {"args": {}, "result": {}}

    monkeypatch.setattr(main.job_service, "get_scoped", get_scoped)

    with pytest.raises(HTTPException) as exc:
        await main.api_job_logs("job-without-cartridge", user=_user())

    assert exc.value.status_code == 422
    assert "job cartridge is unavailable" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_api_dataset_detail_returns_complete_contract(console_main, monkeypatch):
    main = console_main
    calls: list[tuple[str, dict]] = []

    async def fake_refinement(tool, args, **_kwargs):
        calls.append((tool, args))
        if tool == "get_dataset_definition":
            return {
                "name": "sap_successfactors_employee_360",
                "layer": "gold",
                "sql": "select * from employee_360",
                "row_count": 1288,
                "cartridge": "sap_successfactors",
                "source_load_date": "2026-06-11",
                "source_batch_id": "batch-1",
                "updated_at": "2026-06-11T01:00:00Z",
                "sources": ["sap_successfactors_EmpJob"],
                "metadata": {"owner": "demo"},
            }
        if tool == "get_schema":
            return {"columns": [{"name": "user_id", "type": "string"}, "company_id"]}
        raise AssertionError(tool)

    monkeypatch.setattr(main, "_refinement_invoke", fake_refinement)

    detail = await main.api_dataset_detail("sap_successfactors_employee_360", user=_user())

    assert detail["name"] == "sap_successfactors_employee_360"
    assert detail["layer"] == "gold"
    assert detail["type"] == "gold"
    assert detail["sql"] == "select * from employee_360"
    assert detail["columns"] == [{"name": "user_id", "type": "string"}, {"name": "company_id"}]
    assert detail["metadata"]["owner"] == "demo"
    assert detail["metadata"]["sources"] == ["sap_successfactors_EmpJob"]
    assert detail["source_load_date"] == "2026-06-11"
    assert detail["source_batch_id"] == "batch-1"
    assert detail["status"] == "ok"
    assert detail["error"] is None
    assert detail["row_count"] == 1288
    assert detail["cartridge"] == "sap_successfactors"
    assert detail["updated_at"] == "2026-06-11T01:00:00Z"
    assert calls == [
        ("get_dataset_definition", {"name": "sap_successfactors_employee_360"}),
        ("get_schema", {"name": "sap_successfactors_employee_360"}),
    ]


@pytest.mark.asyncio
async def test_api_dataset_detail_marks_partial_schema_failure_without_losing_definition(console_main, monkeypatch):
    main = console_main

    async def fake_refinement(tool, _args, **_kwargs):
        if tool == "get_dataset_definition":
            return {
                "name": "sap_successfactors_employee_360",
                "layer": "gold",
                "row_count": None,
                "cartridge": "sap_successfactors",
            }
        if tool == "get_schema":
            raise HTTPException(404, "Dataset no materializado")
        raise AssertionError(tool)

    monkeypatch.setattr(main, "_refinement_invoke", fake_refinement)

    detail = await main.api_dataset_detail("sap_successfactors_employee_360", user=_user())

    assert detail["name"] == "sap_successfactors_employee_360"
    assert detail["columns"] == []
    assert detail["status"] == "unavailable"
    assert detail["error"] == "Dataset no materializado"


@pytest.mark.asyncio
async def test_dataset_data_propagates_refinement_http_error(console_main, monkeypatch):
    main = console_main

    class FakeResponse:
        status_code = 503

        def json(self):
            return {"detail": "Fuente temporalmente no disponible"}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    request = main.Request({
        "type": "http",
        "method": "GET",
        "path": "/datasets/sap_successfactors_employee_360/data",
        "headers": [],
    })
    request.state.user = _user()
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeClient)

    with pytest.raises(HTTPException) as exc:
        await main.dataset_data("sap_successfactors_employee_360", request, limit=20)

    assert exc.value.status_code == 503
    assert exc.value.detail == "Fuente temporalmente no disponible"


@pytest.mark.asyncio
async def test_dataset_data_maps_refinement_payload_error_to_http_error(console_main, monkeypatch):
    main = console_main

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"error": "SOURCE_FILES_MISSING: no files found for dataset"}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    request = main.Request({
        "type": "http",
        "method": "GET",
        "path": "/datasets/sap_successfactors_employee_360/data",
        "headers": [],
    })
    request.state.user = _user()
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeClient)

    with pytest.raises(HTTPException) as exc:
        await main.dataset_data("sap_successfactors_employee_360", request, limit=20)

    assert exc.value.status_code == 404
    assert "SOURCE_FILES_MISSING" in str(exc.value.detail)
