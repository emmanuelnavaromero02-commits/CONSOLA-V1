from __future__ import annotations

import importlib
import sys
import types

import pytest
from fastapi import HTTPException


INTERNAL_KEY = "test_internal_api_key_with_more_than_32_chars"


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


async def _noop_async(*args, **kwargs):
    return None


async def _empty_list_async(*args, **kwargs):
    return []


class _FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def console_main(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", INTERNAL_KEY)
    monkeypatch.setenv("JWT_SECRET_KEY", "test_jwt_secret_key_with_more_than_32_chars")
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15")

    auth_stub = _module(
        COOKIE_NAME="mod_session",
        REFRESH_COOKIE_NAME="refresh_token",
        close_pool=_noop_async,
        cookie_secure=lambda: False,
        verify_internal_api_key=lambda *args, **kwargs: None,
    )

    asyncpg_stub = _module(executed=[], fetch_rows=[])

    class FakePool:
        async def execute(self, query, *args):
            asyncpg_stub.executed.append((query, args))
            return "OK"

        async def fetch(self, *args, **kwargs):
            return list(asyncpg_stub.fetch_rows)

        async def fetchrow(self, *args, **kwargs):
            return asyncpg_stub.fetch_rows[0] if asyncpg_stub.fetch_rows else None

        async def close(self):
            return None

    async def create_pool(*args, **kwargs):
        return FakePool()

    asyncpg_stub.create_pool = create_pool

    service_stubs = {
        "app.services.auth": auth_stub,
        "app.services.tokens": _module(close_pool=_noop_async),
        "app.services.email_service": _module(),
        "app.services.mcp_registry": _module(
            invoke=_noop_async,
            startup=_noop_async,
            health_check_all=_noop_async,
            close_pool=_noop_async,
        ),
        "app.services.assistant": _module(),
        "app.services.studio_assistant": _module(),
        "app.services.token_store": _module(close_pool=_noop_async),
        "app.services.job_service": _module(list_recent=_empty_list_async, close_pool=_noop_async),
        "app.services.cartridge_service": _module(get_cartridge=_noop_async, close_pool=_noop_async),
    }
    for name, mod in service_stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg_stub)

    sys.modules.pop("app.main", None)
    sys.modules.pop("app.dependencies", None)
    main = importlib.import_module("app.main")
    main._test_asyncpg_stub = asyncpg_stub
    yield main
    sys.modules.pop("app.main", None)
    sys.modules.pop("app.dependencies", None)


@pytest.mark.anyio
async def test_dag_based_cartridge_triggers_airflow_dag(console_main, monkeypatch):
    async def metadata(cartridge, entity):
        return {
            "pattern": "dag-based",
            "entity": entity,
            "dag_id": "replicon_extract",
            "mode": "full",
            "enabled": True,
            "primary_key": "department_id",
        }

    calls = []

    async def invoke(server, tool, args):
        calls.append((server, tool, args))
        return {"dag_run_id": "manual__test", "state": "queued"}

    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)
    monkeypatch.setattr(console_main.mcp_registry, "invoke", invoke)

    result = await console_main.api_pipeline_extract("replicon", "Department", {})

    assert calls == [
        (
            "infra",
            "airflow_trigger_dag",
            {
                "dag_id": "replicon_extract",
                "conf": {"entity": "Department", "mode": "full"},
            },
        )
    ]
    assert result["triggered"] is True
    assert result["cartridge"] == "replicon"
    assert result["entity"] == "Department"
    assert result["dag_id"] == "replicon_extract"
    assert result["run_id"] == "manual__test"
    assert console_main._test_asyncpg_stub.executed
    _, args = console_main._test_asyncpg_stub.executed[-1]
    assert args[0] == "manual__test"
    assert args[1] == "replicon_extract"
    assert args[2] == "replicon"
    assert args[3] == "Department"
    assert args[5] == "full"
    assert args[6] == "queued"


@pytest.mark.anyio
async def test_dag_based_incremental_conf_preserves_dates(console_main, monkeypatch):
    async def metadata(cartridge, entity):
        return {
            "pattern": "dag-based",
            "entity": entity,
            "dag_id": "replicon_extract",
            "mode": "incremental",
            "enabled": True,
            "primary_key": "entry_id",
        }

    calls = []

    async def invoke(server, tool, args):
        calls.append((server, tool, args))
        return {"dag_run_id": "manual__incremental", "state": "queued"}

    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)
    monkeypatch.setattr(console_main.mcp_registry, "invoke", invoke)

    await console_main.api_pipeline_extract(
        "replicon",
        "TimeEntry",
        {"mode": "incremental", "from_date": "2026-01-01", "to_date": "2026-01-31"},
    )

    assert calls[0][2]["conf"] == {
        "entity": "TimeEntry",
        "mode": "incremental",
        "from_date": "2026-01-01",
        "to_date": "2026-01-31",
    }


@pytest.mark.anyio
async def test_mcp_based_cartridge_keeps_mcp_invoke_fallback(console_main, monkeypatch):
    async def metadata(cartridge, entity):
        return {"pattern": "mcp", "entity": entity}

    calls = []

    async def invoke(server, tool, args):
        calls.append((server, tool, args))
        return {"job_id": "job-1"}

    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)
    monkeypatch.setattr(console_main.mcp_registry, "invoke", invoke)

    result = await console_main.api_pipeline_extract("some_server", "Customer", {"mode": "full"})

    assert calls == [("some_server", "extract", {"entity": "Customer", "mode": "full"})]
    assert result == {"job_id": "job-1"}


@pytest.mark.anyio
async def test_dag_based_missing_entity_returns_404(console_main, monkeypatch):
    async def metadata(cartridge, entity):
        return {"pattern": "dag-based", "entity": None}

    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)

    with pytest.raises(HTTPException) as exc:
        await console_main.api_pipeline_extract("replicon", "Missing", {})

    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_dag_based_disabled_entity_returns_400(console_main, monkeypatch):
    async def metadata(cartridge, entity):
        return {
            "pattern": "dag-based",
            "entity": entity,
            "dag_id": "replicon_extract",
            "mode": "full",
            "enabled": False,
        }

    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)

    with pytest.raises(HTTPException) as exc:
        await console_main.api_pipeline_extract("replicon", "Department", {})

    assert exc.value.status_code == 400


def test_bronze_latest_date_from_real_minio_paths(console_main):
    latest_date = console_main._bronze_latest_date_from_objects(
        "replicon",
        "Department",
        [
            "raw/replicon/Department/load_date=2026-05-08/data.parquet",
            "raw/replicon/Department/load_date=2026-05-09/data.parquet",
            "silver/replicon/replicon_department_latest/data.parquet",
        ],
    )

    assert latest_date == "2026-05-09"


@pytest.mark.anyio
async def test_api_pipeline_uses_physical_bronze_when_run_metadata_missing(console_main, monkeypatch):
    async def get_cartridge(cartridge):
        return {
            "entities": [
                {"id": "Department", "mode": "full", "description": "Departments"},
            ]
        }

    async def physical_snapshot(cartridge, entity):
        assert cartridge == "replicon"
        assert entity == "Department"
        return {"latest_date": "2026-05-09", "record_count": 3}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return _FakeResponse({
                "datasets": [
                    {
                        "name": "replicon_department_latest",
                        "layer": "silver",
                        "sources": ["raw/replicon/Department"],
                        "row_count": 3,
                        "last_refresh": "2026-05-09T00:00:00+00:00",
                    }
                ]
            })

    monkeypatch.setattr(console_main.cartridge_service, "get_cartridge", get_cartridge)
    monkeypatch.setattr(console_main, "_bronze_physical_snapshot", physical_snapshot)
    monkeypatch.setattr(console_main.httpx, "AsyncClient", FakeAsyncClient)

    result = await console_main.api_pipeline("replicon")
    department = result["pipeline"][0]

    assert department["entity"] == "Department"
    assert department["bronze"]["source"] == "raw/replicon/Department"
    assert department["bronze"]["latest_date"] == "2026-05-09"
    assert department["bronze"]["record_count"] == 3
    assert department["bronze"]["status"] != "never"
    assert department["silver"][0]["name"] == "replicon_department_latest"
    assert department["silver"][0]["row_count"] == 3


@pytest.mark.anyio
async def test_api_pipeline_returns_dag_last_run_and_last_job(console_main, monkeypatch):
    async def get_cartridge(cartridge):
        return {
            "entities": [
                {"id": "Department", "mode": "full", "description": "Departments"},
            ]
        }

    async def physical_snapshot(cartridge, entity):
        return {"latest_date": "2026-05-09", "record_count": 3}

    async def invoke(server, tool, args):
        assert server == "infra"
        assert tool == "airflow_get_run_status"
        assert args == {"dag_id": "replicon_extract", "dag_run_id": "manual__test"}
        return {
            "state": "success",
            "start_date": "2026-05-09T04:09:57+00:00",
            "end_date": "2026-05-09T04:10:00+00:00",
        }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return _FakeResponse({
                "datasets": [
                    {
                        "name": "replicon_department_latest",
                        "layer": "silver",
                        "sources": ["raw/replicon/Department"],
                        "row_count": 3,
                        "last_refresh": "2026-05-09T04:10:00+00:00",
                    }
                ]
            })

    console_main._test_asyncpg_stub.fetch_rows = [{
        "run_id": "manual__test",
        "dag_id": "replicon_extract",
        "entity": "Department",
        "airflow_dag_run_id": "manual__test",
        "status": "queued",
        "mode": "full",
        "started_at": "2026-05-09T04:09:56+00:00",
        "finished_at": None,
        "record_count": None,
        "bytes_written": None,
        "storage_uri": None,
        "duration_seconds": None,
        "watermark_updated_to": None,
        "error_message": None,
        "extra": {"raw_conf": {"entity": "Department", "mode": "full"}},
    }]
    monkeypatch.setattr(console_main.cartridge_service, "get_cartridge", get_cartridge)
    monkeypatch.setattr(console_main, "_bronze_physical_snapshot", physical_snapshot)
    monkeypatch.setattr(console_main.mcp_registry, "invoke", invoke)
    monkeypatch.setattr(console_main.httpx, "AsyncClient", FakeAsyncClient)

    result = await console_main.api_pipeline("replicon")
    department = result["pipeline"][0]

    assert department["bronze"]["status"] != "never"
    assert department["bronze"]["record_count"] == 3
    assert department["silver"][0]["status"] == "fresh"
    assert department["silver"][0]["row_count"] == 3
    assert department["last_run"]["dag_id"] == "replicon_extract"
    assert department["last_run"]["dag_run_id"] == "manual__test"
    assert department["last_run"]["status"] == "success"
    assert department["last_job"]["dag_id"] == "replicon_extract"
    assert department["last_job"]["dag_run_id"] == "manual__test"
    assert department["last_job"]["status"] == "success"
    assert department["last_job"]["mode"] == "full"
    assert department["last_job"]["duration_sec"] == 3.0


@pytest.mark.anyio
async def test_api_pipeline_entity_runs_returns_recent_history(console_main, monkeypatch):
    async def metadata(cartridge, entity):
        return {
            "pattern": "dag-based",
            "entity": entity,
            "dag_id": "replicon_extract",
            "mode": "full",
            "enabled": True,
        }

    console_main._test_asyncpg_stub.fetch_rows = [{
        "run_id": "manual__test",
        "dag_id": "replicon_extract",
        "airflow_dag_run_id": "manual__test",
        "status": "success",
        "mode": "full",
        "started_at": "2026-05-09T04:09:57+00:00",
        "finished_at": "2026-05-09T04:10:00+00:00",
        "duration_seconds": 3.0,
        "error_message": None,
    }]
    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)

    result = await console_main.api_pipeline_entity_runs("replicon", "Department", limit=20)

    assert result["cartridge"] == "replicon"
    assert result["entity"] == "Department"
    assert result["runs"] == [{
        "dag_id": "replicon_extract",
        "dag_run_id": "manual__test",
        "status": "success",
        "mode": "full",
        "triggered_at": "2026-05-09T04:09:57+00:00",
        "started_at": "2026-05-09T04:09:57+00:00",
        "finished_at": "2026-05-09T04:10:00+00:00",
        "duration_sec": 3.0,
        "error": None,
    }]


@pytest.mark.anyio
async def test_api_pipeline_run_logs_returns_summary(console_main, monkeypatch):
    async def metadata(cartridge, entity):
        return {
            "pattern": "dag-based",
            "entity": entity,
            "dag_id": "replicon_extract",
            "mode": "full",
            "enabled": True,
        }

    calls = []

    async def invoke(server, tool, args):
        calls.append((server, tool, args))
        if tool == "airflow_list_task_instances":
            return {"tasks": [{"task_id": "extract", "state": "success", "duration": 1.5}]}
        if tool == "airflow_get_task_logs":
            return {"logs": "extract ok", "dag_id": args["dag_id"], "task_id": args["task_id"]}
        return {}

    console_main._test_asyncpg_stub.fetch_rows = [{
        "run_id": "manual__test",
        "dag_id": "replicon_extract",
        "airflow_dag_run_id": "manual__test",
        "status": "success",
        "mode": "full",
        "started_at": "2026-05-09T04:09:57+00:00",
        "finished_at": "2026-05-09T04:10:00+00:00",
        "duration_seconds": 3.0,
        "error_message": None,
    }]
    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)
    monkeypatch.setattr(console_main.mcp_registry, "invoke", invoke)

    result = await console_main.api_pipeline_run_logs("replicon", "Department", "manual__test")

    assert result["cartridge"] == "replicon"
    assert result["entity"] == "Department"
    assert result["dag_id"] == "replicon_extract"
    assert result["dag_run_id"] == "manual__test"
    assert result["status"] == "success"
    assert result["available"] is True
    assert result["tasks"] == [{"task_id": "extract", "state": "success", "duration": 1.5}]
    assert result["logs"] == [{"task_id": "extract", "available": True, "logs": "extract ok"}]
    assert result["error"] is None
