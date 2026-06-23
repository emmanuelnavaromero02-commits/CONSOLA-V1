from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException


INTERNAL_KEY = "test_internal_api_key_with_more_than_32_chars"


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def test_bronze_latest_date_from_scoped_tenant_workspace_paths(console_main):
    latest = console_main._bronze_latest_date_from_objects(
        "sap_successfactors",
        "FOCompany",
        [
            "raw/sap_successfactors/FOCompany/tenant_id=t1/workspace_id=w1/load_date=2026-06-07/batch_id=a/data.parquet",
            "raw/sap_successfactors/FOCompany/tenant_id=t1/workspace_id=w1/load_date=2026-06-08/batch_id=b/data.parquet",
        ],
    )

    assert latest == "2026-06-08"


async def _noop_async(*args, **kwargs):
    return None


async def _empty_list_async(*args, **kwargs):
    return []


def _scoped_pipeline_user() -> dict:
    return {
        "id": 10,
        "email": "workspace-admin@example.test",
        "role": "workspace_admin",
        "active_tenant_id": "11111111-1111-1111-1111-111111111111",
        "active_workspace_id": "22222222-2222-2222-2222-222222222222",
        "allowed_cartridges": ["replicon"],
    }


def _scoped_sf_pipeline_user() -> dict:
    user = dict(_scoped_pipeline_user())
    user["allowed_cartridges"] = ["sap_successfactors"]
    return user


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
    monkeypatch.setenv(
        "JWT_SECRET_KEY", "unit_signing_material_aaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv(
        "SECURITY_CONTEXT_SIGNING_KEY",
        "unit_security_context_signing_key_32_chars",
    )

    auth_stub = _module(
        COOKIE_NAME="mod_session",
        REFRESH_COOKIE_NAME="refresh_token",
        close_pool=_noop_async,
        cookie_secure=lambda: False,
        verify_internal_api_key=lambda *args, **kwargs: None,
    )

    asyncpg_stub = _module(executed=[], fetch_rows=[])

    class FakePool:
        def acquire(self):
            return self

        def transaction(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

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
        "app.services.studio_assistant": _module(
            register_local_tool=lambda *args, **kwargs: None
        ),
        "app.services.token_store": _module(close_pool=_noop_async),
        "app.services.job_service": _module(
            list_recent=_empty_list_async, close_pool=_noop_async
        ),
        "app.services.cartridge_service": _module(
            get_cartridge=_noop_async, close_pool=_noop_async
        ),
    }
    for name, mod in service_stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg_stub)

    # Also patch package attributes so `from app.services import xxx` gets the stub
    import app.services as _svc_pkg

    for attr, mod in [
        ("auth", service_stubs["app.services.auth"]),
        ("job_service", service_stubs["app.services.job_service"]),
        ("token_store", service_stubs["app.services.token_store"]),
        ("mcp_registry", service_stubs["app.services.mcp_registry"]),
        ("cartridge_service", service_stubs["app.services.cartridge_service"]),
    ]:
        monkeypatch.setattr(_svc_pkg, attr, mod, raising=False)

    sys.modules.pop("app.main", None)
    sys.modules.pop("app.dependencies", None)
    main = importlib.import_module("app.main")
    main._test_asyncpg_stub = asyncpg_stub
    yield main
    sys.modules.pop("app.routers.studio", None)
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

    async def invoke(server, tool, args, **_kwargs):
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
                "conf": {
                    "cartridge_id": "replicon",
                    "entity": "Department",
                    "mode": "full",
                },
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
async def test_record_dag_pipeline_trigger_sets_rls_scope(console_main, monkeypatch):
    async def table_has_column(table, column):
        return table == "pipeline_runs" and column in {"tenant_id", "workspace_id"}

    monkeypatch.setattr(console_main, "_table_has_column", table_has_column)

    await console_main._record_dag_pipeline_trigger(
        cartridge="sap_successfactors",
        entity="Candidate",
        dag_id="sap_successfactors_extract",
        dag_run_id="manual__scoped",
        mode="incremental",
        status="queued",
        conf={"tenant_id": "11111111-1111-1111-1111-111111111111"},
        tenant_id="11111111-1111-1111-1111-111111111111",
        workspace_id="22222222-2222-2222-2222-222222222222",
    )

    queries = [query for query, _args in console_main._test_asyncpg_stub.executed]
    assert "set_config('app.tenant_id'" in queries[0]
    assert console_main._test_asyncpg_stub.executed[0][1] == (
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    )
    assert "INSERT INTO pipeline_runs" in queries[-1]


@pytest.mark.anyio
async def test_fetch_sync_run_sets_rls_scope_before_select(console_main, monkeypatch):
    async def table_has_column(table, column):
        return table == "pipeline_runs" and column in {"tenant_id", "workspace_id"}

    monkeypatch.setattr(console_main, "_table_has_column", table_has_column)
    console_main._test_asyncpg_stub.executed.clear()
    console_main._test_asyncpg_stub.fetch_rows = [
        {
            "run_id": "sync_now:replicon:test",
            "dag_id": "sync_now",
            "cartridge_id": "replicon",
            "entity": "__sync_now__",
            "mode": "incremental",
            "status": "running",
            "started_at": None,
            "finished_at": None,
            "error_message": None,
            "extra": {},
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "workspace_id": "22222222-2222-2222-2222-222222222222",
        }
    ]

    row = await console_main._fetch_sync_run(
        cartridge="replicon",
        run_id="sync_now:replicon:test",
        user=_scoped_pipeline_user(),
    )

    assert row and row["run_id"] == "sync_now:replicon:test"
    queries = [query for query, _args in console_main._test_asyncpg_stub.executed]
    assert "set_config('app.tenant_id'" in queries[0]
    assert console_main._test_asyncpg_stub.executed[0][1] == (
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    )


@pytest.mark.anyio
async def test_fetch_active_sync_run_sets_rls_scope(console_main, monkeypatch):
    async def table_has_column(table, column):
        return table == "pipeline_runs" and column in {"tenant_id", "workspace_id"}

    monkeypatch.setattr(console_main, "_table_has_column", table_has_column)
    console_main._test_asyncpg_stub.executed.clear()
    console_main._test_asyncpg_stub.fetch_rows = [
        {
            "run_id": "sync_now:replicon:active",
            "dag_id": "sync_now",
            "cartridge_id": "replicon",
            "entity": "__sync_now__",
            "mode": "incremental",
            "status": "running",
            "started_at": None,
            "finished_at": None,
            "error_message": None,
            "extra": {"target": "all"},
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "workspace_id": "22222222-2222-2222-2222-222222222222",
        }
    ]

    row = await console_main._fetch_active_sync_run(
        cartridge="replicon",
        mode="incremental",
        target="all",
        conn_id=None,
        user=_scoped_pipeline_user(),
    )

    assert row and row["run_id"] == "sync_now:replicon:active"
    queries = [query for query, _args in console_main._test_asyncpg_stub.executed]
    assert "set_config('app.tenant_id'" in queries[0]


@pytest.mark.anyio
async def test_extract_all_scopes_idempotency_key_per_entity(console_main, monkeypatch):
    async def pipeline(cartridge, user=None):
        return {
            "pipeline": [
                {"entity": "TimeEntry"},
                {"entity": "Project"},
            ]
        }

    calls = []

    async def extract(cartridge, entity, body, user=None):
        calls.append((entity, dict(body)))
        return {"dag_run_id": f"manual__{entity}", "state": "queued"}

    monkeypatch.setattr(console_main, "api_pipeline", pipeline)
    monkeypatch.setattr(console_main, "api_pipeline_extract", extract)

    result = await console_main.api_pipeline_extract_all(
        "replicon",
        {"mode": "incremental", "idempotency_key": "sync_now:replicon:test"},
        user=_scoped_pipeline_user(),
    )

    assert result["count"] == 2
    assert calls[0][1]["idempotency_key"] == "sync_now:replicon:test:TimeEntry"
    assert calls[1][1]["idempotency_key"] == "sync_now:replicon:test:Project"


@pytest.mark.anyio
async def test_sync_now_successfactors_uses_aggregate_extract_all_dag(
    console_main, monkeypatch
):
    user = _scoped_sf_pipeline_user()
    stored: dict = {}
    trigger_calls: list[dict] = []
    records: list[dict] = []

    async def resolve(user_arg, cartridge_id, fallback=None):
        assert cartridge_id == "sap_successfactors"
        return "sap_successfactors", True

    async def no_active(**_kwargs):
        return None

    async def upsert(**kwargs):
        stored.clear()
        stored.update(
            {
                "run_id": kwargs["run_id"],
                "dag_id": console_main._SYNC_NOW_DAG_ID,
                "cartridge_id": kwargs["cartridge"],
                "entity": console_main._SYNC_NOW_ENTITY,
                "mode": kwargs["mode"],
                "status": kwargs["status"],
                "started_at": None,
                "finished_at": None,
                "error_message": kwargs.get("error_message"),
                "extra": kwargs["extra"],
            }
        )

    async def fetch(**_kwargs):
        return dict(stored)

    async def build_status(*, cartridge, row, user=None):
        return console_main._sync_public_payload(row, row["extra"])

    async def trigger(dag_id, conf, user_arg, dag_run_id=None):
        trigger_calls.append({"dag_id": dag_id, "conf": conf, "dag_run_id": dag_run_id})
        return {"dag_run_id": dag_run_id, "state": "queued"}

    async def record(**kwargs):
        records.append(kwargs)

    async def fanout_should_not_run(*_args, **_kwargs):
        raise AssertionError("sap_successfactors sync-now should use aggregate DAG")

    monkeypatch.setattr(console_main, "_resolve_scoped_operation_cartridge", resolve)
    monkeypatch.setattr(console_main, "_fetch_active_sync_run", no_active)
    monkeypatch.setattr(console_main, "_upsert_sync_run", upsert)
    monkeypatch.setattr(console_main, "_fetch_sync_run", fetch)
    monkeypatch.setattr(console_main, "_build_sync_run_status", build_status)
    monkeypatch.setattr(console_main, "_trigger_airflow_extract_dag", trigger)
    monkeypatch.setattr(console_main, "_record_dag_pipeline_trigger", record)
    monkeypatch.setattr(console_main, "api_pipeline_extract_all", fanout_should_not_run)

    result = await console_main.api_cartridge_sync_now(
        "sap_successfactors",
        {"mode": "incremental", "target": "all"},
        user=user,
    )

    assert result["status"] == "running"
    assert trigger_calls[0]["dag_id"] == "sap_successfactors_extract_all"
    assert trigger_calls[0]["dag_run_id"].startswith(
        "console__sap_successfactors_extract_all__"
    )
    assert trigger_calls[0]["conf"]["tenant_id"] == user["active_tenant_id"]
    assert trigger_calls[0]["conf"]["workspace_id"] == user["active_workspace_id"]
    assert records[0]["entity"] == console_main._SYNC_AGGREGATE_ENTITY
    assert stored["extra"]["trigger_strategy"] == "aggregate_dag"
    assert (
        stored["extra"]["triggered_entities"][0]["entity"]
        == console_main._SYNC_AGGREGATE_ENTITY
    )


@pytest.mark.anyio
async def test_sync_now_continues_after_active_run_reconciles_terminal(
    console_main, monkeypatch
):
    user = _scoped_sf_pipeline_user()
    trigger_calls: list[dict] = []
    stored: dict = {}
    old_row = {
        "run_id": "sync_now:sap_successfactors:old",
        "dag_id": console_main._SYNC_NOW_DAG_ID,
        "cartridge_id": "sap_successfactors",
        "entity": console_main._SYNC_NOW_ENTITY,
        "mode": "incremental",
        "status": "running",
        "started_at": datetime.now(timezone.utc) - timedelta(hours=3),
        "finished_at": None,
        "error_message": None,
        "extra": {"target": "all"},
    }

    async def resolve(user_arg, cartridge_id, fallback=None):
        return "sap_successfactors", True

    async def active(**_kwargs):
        return old_row

    async def upsert(**kwargs):
        stored.clear()
        stored.update(
            {
                "run_id": kwargs["run_id"],
                "dag_id": console_main._SYNC_NOW_DAG_ID,
                "cartridge_id": kwargs["cartridge"],
                "entity": console_main._SYNC_NOW_ENTITY,
                "mode": kwargs["mode"],
                "status": kwargs["status"],
                "started_at": None,
                "finished_at": None,
                "error_message": kwargs.get("error_message"),
                "extra": kwargs["extra"],
            }
        )

    async def fetch(**_kwargs):
        return dict(stored)

    async def build_status(*, cartridge, row, user=None):
        if row["run_id"] == old_row["run_id"]:
            return {"run_id": row["run_id"], "status": "failed"}
        return console_main._sync_public_payload(row, row["extra"])

    async def trigger(dag_id, conf, user_arg, dag_run_id=None):
        trigger_calls.append({"dag_id": dag_id, "dag_run_id": dag_run_id})
        return {"dag_run_id": dag_run_id, "state": "queued"}

    async def record(**_kwargs):
        return None

    monkeypatch.setattr(console_main, "_resolve_scoped_operation_cartridge", resolve)
    monkeypatch.setattr(console_main, "_fetch_active_sync_run", active)
    monkeypatch.setattr(console_main, "_upsert_sync_run", upsert)
    monkeypatch.setattr(console_main, "_fetch_sync_run", fetch)
    monkeypatch.setattr(console_main, "_build_sync_run_status", build_status)
    monkeypatch.setattr(console_main, "_trigger_airflow_extract_dag", trigger)
    monkeypatch.setattr(console_main, "_record_dag_pipeline_trigger", record)

    result = await console_main.api_cartridge_sync_now(
        "sap_successfactors",
        {"mode": "incremental", "target": "all"},
        user=user,
    )

    assert result["run_id"] != old_row["run_id"]
    assert result["status"] == "running"
    assert trigger_calls


@pytest.mark.anyio
async def test_build_sync_run_status_marks_stale_queued_children_failed(
    console_main, monkeypatch
):
    user = _scoped_sf_pipeline_user()
    row = {
        "run_id": "sync_now:sap_successfactors:stale",
        "dag_id": console_main._SYNC_NOW_DAG_ID,
        "cartridge_id": "sap_successfactors",
        "entity": console_main._SYNC_NOW_ENTITY,
        "mode": "incremental",
        "status": "running",
        "started_at": datetime.now(timezone.utc) - timedelta(hours=2),
        "finished_at": None,
        "error_message": None,
        "extra": {
            "target": "all",
            "mode": "incremental",
            "triggered_entities": [
                {"entity": "__extract_all__", "dag_run_id": "child-run"}
            ],
            "errors": [],
            "steps": console_main._initial_sync_steps(),
        },
    }
    upserts: list[dict] = []

    async def child_runs(**_kwargs):
        return [{"run_id": "child-run", "status": "queued"}]

    async def pipeline(cartridge, user=None):
        return {"pipeline": []}

    async def upsert(**kwargs):
        upserts.append(kwargs)

    async def fetch(**_kwargs):
        updated = dict(row)
        updated["status"] = upserts[-1]["status"]
        updated["extra"] = upserts[-1]["extra"]
        updated["error_message"] = upserts[-1].get("error_message")
        return updated

    monkeypatch.setattr(console_main, "_sync_child_runs", child_runs)
    monkeypatch.setattr(console_main, "api_pipeline", pipeline)
    monkeypatch.setattr(console_main, "_upsert_sync_run", upsert)
    monkeypatch.setattr(console_main, "_fetch_sync_run", fetch)
    monkeypatch.setattr(console_main, "_SYNC_NOW_STALE_AFTER_SECONDS", 60)

    result = await console_main._build_sync_run_status(
        cartridge="sap_successfactors",
        row=row,
        user=user,
    )

    assert result["status"] == "failed"
    assert upserts[-1]["status"] == "failed"
    assert any(
        "timed out" in str(error.get("error"))
        for error in upserts[-1]["extra"]["errors"]
    )


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

    async def invoke(server, tool, args, **_kwargs):
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
        "cartridge_id": "replicon",
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

    async def invoke(server, tool, args, **_kwargs):
        calls.append((server, tool, args))
        return {"job_id": "job-1"}

    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)
    monkeypatch.setattr(console_main.mcp_registry, "invoke", invoke)

    result = await console_main.api_pipeline_extract(
        "some_server", "Customer", {"mode": "full"}
    )

    assert calls == [("some_server", "extract", {"entity": "Customer", "mode": "full"})]
    assert result == {"job_id": "job-1"}


@pytest.mark.anyio
async def test_api_pipeline_extract_all_triggers_visible_entities(
    console_main, monkeypatch
):
    async def pipeline(cartridge):
        assert cartridge == "replicon"
        return {
            "pipeline": [
                {"entity": "Activity"},
                {"entity": "Department"},
            ]
        }

    calls = []

    async def extract(cartridge, entity, body):
        calls.append((cartridge, entity, body))
        return {
            "triggered": True,
            "entity": entity,
            "dag_run_id": f"manual__{entity}",
            "dag_id": f"replicon_{entity.lower()}",
            "state": "queued",
        }

    monkeypatch.setattr(console_main, "api_pipeline", pipeline)
    monkeypatch.setattr(console_main, "api_pipeline_extract", extract)

    result = await console_main.api_pipeline_extract_all(
        "replicon", {"mode": "incremental"}
    )

    assert calls == [
        ("replicon", "Activity", {"mode": "incremental"}),
        ("replicon", "Department", {"mode": "incremental"}),
    ]
    assert result["count"] == 2
    assert result["error_count"] == 0
    assert [item["entity"] for item in result["triggered"]] == [
        "Activity",
        "Department",
    ]
    assert result["triggered"][0]["job_id"] == "manual__Activity"


@pytest.mark.anyio
async def test_api_pipeline_extract_all_reports_per_entity_errors(
    console_main, monkeypatch
):
    async def pipeline(cartridge):
        return {"pipeline": [{"entity": "Good"}, {"entity": "Bad"}]}

    async def extract(cartridge, entity, body):
        if entity == "Bad":
            raise HTTPException(400, "Entity is disabled")
        return {"job_id": "job-good"}

    monkeypatch.setattr(console_main, "api_pipeline", pipeline)
    monkeypatch.setattr(console_main, "api_pipeline_extract", extract)

    result = await console_main.api_pipeline_extract_all("replicon", {})

    assert result["count"] == 1
    assert result["error_count"] == 1
    assert result["triggered"][0]["entity"] == "Good"
    assert result["errors"] == [
        {"entity": "Bad", "status_code": 400, "error": "Entity is disabled"}
    ]


@pytest.mark.anyio
async def test_dag_based_missing_entity_returns_404(console_main, monkeypatch):
    async def metadata(cartridge, entity):
        return {"pattern": "dag-based", "entity": None}

    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)

    with pytest.raises(HTTPException) as exc:
        await console_main.api_pipeline_extract("replicon", "Missing", {})

    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_dag_based_orphan_static_entity_without_scoped_config_returns_400(
    console_main, monkeypatch
):
    async def metadata(cartridge, entity):
        return {"pattern": "dag-based", "entity": None}

    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)
    monkeypatch.setattr(
        console_main,
        "_entity_declared_in_static_catalog",
        lambda cartridge, entity: cartridge == "sap_successfactors"
        and entity == "PerPhone",
        raising=False,
    )

    with pytest.raises(HTTPException) as exc:
        await console_main.api_pipeline_extract("sap_successfactors", "PerPhone", {})

    assert exc.value.status_code == 400
    assert "entity_config" in str(exc.value.detail)


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
async def test_api_pipeline_uses_physical_bronze_when_run_metadata_missing(
    console_main, monkeypatch
):
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
            return _FakeResponse(
                {
                    "datasets": [
                        {
                            "name": "replicon_department_latest",
                            "layer": "silver",
                            "sources": ["raw/replicon/Department"],
                            "row_count": 3,
                            "last_refresh": "2026-05-09T00:00:00+00:00",
                        }
                    ]
                }
            )

    monkeypatch.setattr(console_main.cartridge_service, "get_cartridge", get_cartridge)
    monkeypatch.setattr(console_main, "_bronze_physical_snapshot", physical_snapshot)
    monkeypatch.setattr(console_main.httpx, "AsyncClient", FakeAsyncClient)

    result = await console_main.api_pipeline("replicon", user=_scoped_pipeline_user())
    department = result["pipeline"][0]

    assert department["entity"] == "Department"
    assert department["bronze"]["source"] == "raw/replicon/Department"
    assert department["bronze"]["latest_date"] == "2026-05-09"
    assert department["bronze"]["record_count"] == 3
    assert department["bronze"]["status"] != "never"
    assert department["silver"][0]["name"] == "replicon_department_latest"
    assert department["silver"][0]["row_count"] == 3


@pytest.mark.anyio
async def test_api_pipeline_returns_dag_last_run_and_last_job(
    console_main, monkeypatch
):
    async def get_cartridge(cartridge):
        return {
            "entities": [
                {"id": "Department", "mode": "full", "description": "Departments"},
            ]
        }

    async def physical_snapshot(cartridge, entity):
        return {"latest_date": "2026-05-09", "record_count": 3}

    async def invoke(server, tool, args, **_kwargs):
        assert server == "infra"
        assert tool == "airflow_get_run_status"
        assert args == {"dag_id": "replicon_extract", "dag_run_id": "manual__test"}
        return {
            "state": "success",
            "start_date": "2026-05-09T04:09:57+00:00",
            "end_date": "2026-05-09T04:10:00+00:00",
        }

    _recent_refresh = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return _FakeResponse(
                {
                    "datasets": [
                        {
                            "name": "replicon_department_latest",
                            "layer": "silver",
                            "sources": ["raw/replicon/Department"],
                            "row_count": 3,
                            "last_refresh": _recent_refresh,
                        }
                    ]
                }
            )

    console_main._test_asyncpg_stub.fetch_rows = [
        {
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
        }
    ]
    monkeypatch.setattr(console_main.cartridge_service, "get_cartridge", get_cartridge)
    monkeypatch.setattr(console_main, "_bronze_physical_snapshot", physical_snapshot)
    monkeypatch.setattr(console_main.mcp_registry, "invoke", invoke)
    monkeypatch.setattr(console_main.httpx, "AsyncClient", FakeAsyncClient)

    result = await console_main.api_pipeline("replicon", user=_scoped_pipeline_user())
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
async def test_api_pipeline_entity_runs_returns_recent_history(
    console_main, monkeypatch
):
    async def metadata(cartridge, entity):
        return {
            "pattern": "dag-based",
            "entity": entity,
            "dag_id": "replicon_extract",
            "mode": "full",
            "enabled": True,
        }

    console_main._test_asyncpg_stub.fetch_rows = [
        {
            "run_id": "manual__test",
            "dag_id": "replicon_extract",
            "airflow_dag_run_id": "manual__test",
            "status": "success",
            "mode": "full",
            "started_at": "2026-05-09T04:09:57+00:00",
            "finished_at": "2026-05-09T04:10:00+00:00",
            "duration_seconds": 3.0,
            "error_message": None,
        }
    ]
    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)

    result = await console_main.api_pipeline_entity_runs(
        "replicon", "Department", limit=20
    )

    assert result["cartridge"] == "replicon"
    assert result["entity"] == "Department"
    assert result["runs"] == [
        {
            "dag_id": "replicon_extract",
            "dag_run_id": "manual__test",
            "status": "success",
            "mode": "full",
            "triggered_at": "2026-05-09T04:09:57+00:00",
            "started_at": "2026-05-09T04:09:57+00:00",
            "finished_at": "2026-05-09T04:10:00+00:00",
            "duration_sec": 3.0,
            "error": None,
        }
    ]


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

    async def invoke(server, tool, args, **_kwargs):
        calls.append((server, tool, args))
        if tool == "airflow_list_task_instances":
            return {
                "tasks": [{"task_id": "extract", "state": "success", "duration": 1.5}]
            }
        if tool == "airflow_get_task_logs":
            return {
                "logs": "extract ok",
                "dag_id": args["dag_id"],
                "task_id": args["task_id"],
            }
        return {}

    console_main._test_asyncpg_stub.fetch_rows = [
        {
            "run_id": "manual__test",
            "dag_id": "replicon_extract",
            "airflow_dag_run_id": "manual__test",
            "status": "success",
            "mode": "full",
            "started_at": "2026-05-09T04:09:57+00:00",
            "finished_at": "2026-05-09T04:10:00+00:00",
            "duration_seconds": 3.0,
            "error_message": None,
        }
    ]
    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)
    monkeypatch.setattr(console_main.mcp_registry, "invoke", invoke)

    result = await console_main.api_pipeline_run_logs(
        "replicon", "Department", "manual__test"
    )

    assert result["cartridge"] == "replicon"
    assert result["entity"] == "Department"
    assert result["dag_id"] == "replicon_extract"
    assert result["dag_run_id"] == "manual__test"
    assert result["status"] == "success"
    assert result["available"] is True
    assert result["tasks"] == [
        {"task_id": "extract", "state": "success", "duration": 1.5}
    ]
    assert result["logs"] == [
        {"task_id": "extract", "available": True, "logs": "extract ok"}
    ]
    assert result["error"] is None
