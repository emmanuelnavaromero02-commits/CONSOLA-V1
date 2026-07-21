from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from app.domains.pipeline import run_state


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


def test_pipeline_gold_dependency_matcher_accepts_successfactors_s3_paths(console_main):
    assert run_state.pipeline_gold_dependencies_for_silver(
        "sap_successfactors_employee_profile",
        [
            {
                "name": "sap_successfactors_talent_signals",
                "sources": [
                    "silver/sap_successfactors/sap_successfactors_employee_profile"
                ],
            }
        ],
        cartridge="sap_successfactors",
    ) == [
        {
            "name": "sap_successfactors_talent_signals",
            "sources": [
                "silver/sap_successfactors/sap_successfactors_employee_profile"
            ],
        }
    ]


def test_sync_child_gold_refresh_summary_counts_partial_aggregate(console_main):
    summary = console_main._sync_child_gold_refresh_summary(
        [
            {
                "extra": {
                    "gold_refresh": {
                        "status": "partial",
                        "materialized": 14,
                        "total": 19,
                        "results": [
                            {"name": "ok_dataset", "status": "ok"},
                            {"name": "failed_dataset", "status": "error"},
                        ],
                    }
                }
            }
        ]
    )

    assert summary["status"] == "partial"
    assert summary["materialized"] == 14
    assert summary["total"] == 19
    assert summary["failed"] == 5


def test_sync_step_recomputes_terminal_percent_from_counts(console_main):
    step = console_main._normalize_sync_step_payload(
        {
            "id": "bronze",
            "label": "Bronze",
            "status": "partial",
            "completed": 40,
            "total": 40,
            "percent": 0,
        }
    )

    assert step["percent"] == 100


def test_sync_step_entity_summary_keeps_blocker_reason(console_main):
    summary = console_main._sync_step_entity_summary(
        [
            {
                "entity": "CareerWorksheet",
                "status": "blocked",
                "row_count": 0,
                "extra": {
                    "reason": "entity_not_exposed_in_sap",
                    "fields_missing": ["userId"],
                },
            },
            {"entity": "EmpJob", "status": "success", "row_count": 1288},
        ]
    )

    assert summary["counts"] == {
        "success": 1,
        "partial": 0,
        "blocked": 1,
        "failed": 0,
    }
    assert summary["blockers"][0]["entity"] == "CareerWorksheet"
    assert summary["blockers"][0]["reason"] == "entity_not_exposed_in_sap"


@pytest.mark.anyio
async def test_sync_now_waits_for_extract_all_summary_before_terminal(
    console_main, monkeypatch
):
    user = _scoped_sf_pipeline_user()
    row = {
        "run_id": "sync_now:sap_successfactors:race",
        "dag_id": console_main._SYNC_NOW_DAG_ID,
        "cartridge_id": "sap_successfactors",
        "entity": console_main._SYNC_NOW_ENTITY,
        "mode": "incremental",
        "status": "running",
        "started_at": datetime.now(timezone.utc),
        "finished_at": None,
        "error_message": None,
        "extra": {
            "target": "all",
            "mode": "incremental",
            "triggered_entities": [
                {"entity": "__extract_all__", "dag_run_id": "aggregate-run"}
            ],
            "errors": [],
            "steps": console_main._initial_sync_steps(),
        },
    }
    upserts: list[dict] = []

    async def child_runs(**_kwargs):
        return [
            {
                "run_id": "aggregate-run",
                "entity": "__extract_all__",
                "status": "success",
                "extra": {"raw_conf": {"target": "all"}, "triggered_by": "console"},
            }
        ]

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

    result = await console_main._build_sync_run_status(
        cartridge="sap_successfactors",
        row=row,
        user=user,
    )

    assert result["status"] == "running"
    assert upserts[-1]["extra"]["aggregate_summary_pending"] is True
    assert upserts[-1]["extra"]["extract_all_summary_seen"] is False
    assert any(
        step["id"] == "bronze"
        and step["status"] == "running"
        and "resumen final" in step["detail"]
        for step in result["steps"]
    )


@pytest.mark.anyio
async def test_sync_now_publishes_gold_refresh_to_control_room(
    console_main, monkeypatch
):
    user = _scoped_sf_pipeline_user()
    row = {
        "run_id": "sync_now:sap_successfactors:gold-refresh",
        "dag_id": console_main._SYNC_NOW_DAG_ID,
        "cartridge_id": "sap_successfactors",
        "entity": console_main._SYNC_NOW_ENTITY,
        "mode": "incremental",
        "status": "running",
        "started_at": datetime.now(timezone.utc),
        "finished_at": None,
        "error_message": None,
        "extra": {
            "target": "all",
            "mode": "incremental",
            "triggered_entities": [
                {"entity": "__extract_all__", "dag_run_id": "aggregate-run"}
            ],
            "errors": [],
            "steps": console_main._initial_sync_steps(),
        },
    }
    upserts: list[dict] = []
    intelligence_calls: list[dict] = []

    async def child_runs(**_kwargs):
        return [
            {
                "run_id": "aggregate-run",
                "airflow_dag_run_id": "aggregate-run",
                "entity": "__extract_all__",
                "status": "success",
                "extra": {
                    "summary": {"extracted": 1},
                    "gold_refresh": {
                        "status": "success",
                        "materialized": 1,
                        "total": 1,
                        "results": [
                            {
                                "name": "sap_successfactors_talent_signals",
                                "status": "ok",
                            }
                        ],
                    },
                },
            }
        ]

    async def pipeline(cartridge, user=None):
        return {
            "pipeline": [
                {
                    "entity": "Candidate",
                    "bronze": {"status": "fresh"},
                    "silver": [{"name": "candidate_latest", "status": "fresh"}],
                    "gold": [
                        {
                            "name": "sap_successfactors_talent_signals",
                            "status": "fresh",
                        }
                    ],
                }
            ]
        }

    async def upsert(**kwargs):
        upserts.append(kwargs)

    async def fetch(**_kwargs):
        updated = dict(row)
        updated["status"] = upserts[-1]["status"]
        updated["extra"] = upserts[-1]["extra"]
        updated["error_message"] = upserts[-1].get("error_message")
        return updated

    async def run_intelligence(user_arg, payload, persist=False):
        intelligence_calls.append(
            {"user": user_arg, "payload": payload, "persist": persist}
        )
        return {
            "status": "completed",
            "run_ref": payload["run_ref"],
            "intelligence_run_id": "intel-1",
            "signals": [{"id": "signal-1"}],
            "skipped": [],
        }

    async def refresh_dashboard_state(user=None):
        return {
            "meta": {"source_count": 1, "item_count": 1},
            "summary": {"total_items": 1, "data_ready_sources": 1},
        }

    async def gold_kpis(user=None):
        return {"gold": True}

    async def talent_kpis(user=None):
        return {"talent": True}

    control_room_stub = _module(
        refresh_dashboard_state=refresh_dashboard_state,
        sap_successfactors_gold_kpis=gold_kpis,
        sap_successfactors_talent_kpis=talent_kpis,
    )
    intelligence_stub = _module(run_intelligence=run_intelligence)
    import app.services as _svc_pkg

    monkeypatch.setitem(
        sys.modules, "app.services.control_room_service", control_room_stub
    )
    monkeypatch.setitem(
        sys.modules, "app.services.intelligence_engine", intelligence_stub
    )
    monkeypatch.setattr(
        _svc_pkg, "control_room_service", control_room_stub, raising=False
    )
    monkeypatch.setattr(
        _svc_pkg, "intelligence_engine", intelligence_stub, raising=False
    )
    monkeypatch.setattr(console_main, "_sync_child_runs", child_runs)
    monkeypatch.setattr(console_main, "api_pipeline", pipeline)
    monkeypatch.setattr(console_main, "_upsert_sync_run", upsert)
    monkeypatch.setattr(console_main, "_fetch_sync_run", fetch)

    async def agentops(**_kwargs):
        return {
            "status": "success",
            "total": 0,
            "completed": 0,
            "failed": 0,
            "results": [],
        }

    monkeypatch.setattr(console_main, "_run_sync_agentops_monitors", agentops)

    result = await console_main._build_sync_run_status(
        cartridge="sap_successfactors",
        row=row,
        user=user,
        persist_control_room_state=True,
    )

    assert result["control_room_ready"] is True
    assert intelligence_calls
    call = intelligence_calls[0]
    assert call["persist"] is True
    assert call["payload"]["run_mode"] == "gold_refresh"
    assert call["payload"]["run_ref"] == (
        "gold-refresh:"
        "22222222-2222-2222-2222-222222222222:"
        "sap_successfactors:"
        "aggregate-run"
    )
    assert call["payload"]["datasets"] == ["sap_successfactors_talent_signals"]
    assert upserts[-1]["extra"]["control_room_gold_refresh"]["signals"] == 1


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
    from app.services import db_pool as db_pool_service

    db_pool_service.reset_db_pool_for_tests()

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
    db_pool_service.reset_db_pool_for_tests()
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
    async def table_has_column(table, column, **_kwargs):
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
    async def table_has_column(table, column, **_kwargs):
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
    async def table_has_column(table, column, **_kwargs):
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
async def test_fetch_active_sync_run_tolerates_legacy_pipeline_runs_schema(
    console_main, monkeypatch
):
    class FakeConn:
        def __init__(self):
            self.calls = []

        async def fetchrow(self, query, *args):
            self.calls.append((query, args))
            return {
                "run_id": "sync_now:sap_successfactors:legacy",
                "dag_id": "sync_now",
                "cartridge_id": "sap_successfactors",
                "entity": "__sync_now__",
                "status": "running",
                "extra": {},
            }

    class FakeScope:
        def __init__(self, conn):
            self.conn = conn

        async def __aenter__(self):
            return self.conn, None, None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def table_has_column(table, column, **_kwargs):
        return False

    conn = FakeConn()
    monkeypatch.setattr(console_main, "_table_has_column", table_has_column)
    monkeypatch.setattr(console_main, "_get_db_pool", _noop_async)
    monkeypatch.setattr(
        console_main, "scoped_db_for_user", lambda pool, user: FakeScope(conn)
    )

    row = await console_main._fetch_active_sync_run(
        cartridge="sap_successfactors",
        mode="incremental",
        target="all",
        conn_id=None,
        user=None,
    )

    assert row and row["run_id"] == "sync_now:sap_successfactors:legacy"
    query, args = conn.calls[0]
    assert "COALESCE(mode" not in query
    assert "extra->>" not in query
    assert "started_at >" not in query
    assert "ORDER BY run_id DESC" in query
    assert args[0] == "sap_successfactors"
    assert len(args) == 2


@pytest.mark.anyio
async def test_fetch_active_sync_run_returns_none_on_lookup_error(
    console_main, monkeypatch
):
    class BrokenConn:
        async def fetchrow(self, *_args, **_kwargs):
            raise RuntimeError("schema drift")

    class FakeScope:
        async def __aenter__(self):
            return BrokenConn(), None, None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def table_has_column(table, column, **_kwargs):
        return table == "pipeline_runs" and column in {"mode", "extra", "started_at"}

    monkeypatch.setattr(console_main, "_table_has_column", table_has_column)
    monkeypatch.setattr(console_main, "_get_db_pool", _noop_async)
    monkeypatch.setattr(
        console_main, "scoped_db_for_user", lambda pool, user: FakeScope()
    )

    row = await console_main._fetch_active_sync_run(
        cartridge="sap_successfactors",
        mode="incremental",
        target="all",
        conn_id="femsa_sf",
        user=None,
    )

    assert row is None


@pytest.mark.anyio
async def test_active_sync_run_endpoint_returns_inactive_payload_without_404(
    console_main, monkeypatch
):
    user = _scoped_sf_pipeline_user()

    async def resolve(user_arg, cartridge_id, fallback=None):
        assert cartridge_id == "sap_successfactors"
        return "sap_successfactors", True

    async def fetch_active(**_kwargs):
        return None

    monkeypatch.setattr(console_main, "_resolve_scoped_operation_cartridge", resolve)
    monkeypatch.setattr(console_main, "_fetch_active_sync_run", fetch_active)

    result = await console_main.api_cartridge_active_sync_run(
        "sap_successfactors",
        mode="incremental",
        target="all",
        conn_id="femsa_sf",
        user=user,
    )

    assert result["active"] is False
    assert result["status"] == "skipped"
    assert result["reason"] == "no_active_sync_run"
    assert result["run_id"] is None
    assert result["conn_id"] == "femsa_sf"


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

    async def build_status(
        *, cartridge, row, user=None, persist_control_room_state=False
    ):
        assert persist_control_room_state is True
        return console_main._sync_public_payload(row, row["extra"])

    async def trigger(dag_id, conf, user_arg, dag_run_id=None):
        trigger_calls.append({"dag_id": dag_id, "conf": conf, "dag_run_id": dag_run_id})
        return {"dag_run_id": dag_run_id, "state": "queued"}

    async def record(**kwargs):
        records.append(kwargs)

    async def fanout_should_not_run(*_args, **_kwargs):
        raise AssertionError("sap_successfactors sync-now should use aggregate DAG")

    async def seed_datasets(**kwargs):
        assert kwargs["cartridge"] == "sap_successfactors"
        assert kwargs["user"] is user
        return {"status": "success", "seeded_rows": 59}

    monkeypatch.setattr(console_main, "_resolve_scoped_operation_cartridge", resolve)
    monkeypatch.setattr(console_main, "_fetch_active_sync_run", no_active)
    monkeypatch.setattr(console_main, "_upsert_sync_run", upsert)
    monkeypatch.setattr(console_main, "_fetch_sync_run", fetch)
    monkeypatch.setattr(console_main, "_build_sync_run_status", build_status)
    monkeypatch.setattr(console_main, "_trigger_airflow_extract_dag", trigger)
    monkeypatch.setattr(console_main, "_record_dag_pipeline_trigger", record)
    monkeypatch.setattr(console_main, "_ensure_sync_packaged_datasets", seed_datasets)
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
    assert trigger_calls[0]["conf"]["idempotency_key"] == stored["run_id"]
    assert trigger_calls[0]["conf"]["tenant_id"] == user["active_tenant_id"]
    assert trigger_calls[0]["conf"]["workspace_id"] == user["active_workspace_id"]
    assert records[0]["entity"] == console_main._SYNC_AGGREGATE_ENTITY
    assert stored["extra"]["trigger_strategy"] == "aggregate_dag"
    assert stored["extra"]["dataset_seed"] == {"status": "success", "seeded_rows": 59}
    assert (
        stored["extra"]["triggered_entities"][0]["entity"]
        == console_main._SYNC_AGGREGATE_ENTITY
    )


@pytest.mark.anyio
async def test_api_pipeline_extract_all_successfactors_uses_aggregate_dag(
    console_main, monkeypatch
):
    user = _scoped_sf_pipeline_user()
    trigger_calls: list[dict] = []
    records: list[dict] = []

    async def trigger(dag_id, conf, user_arg, dag_run_id=None):
        trigger_calls.append({"dag_id": dag_id, "conf": conf, "dag_run_id": dag_run_id})
        return {"dag_run_id": dag_run_id, "state": "queued"}

    async def record(**kwargs):
        records.append(kwargs)

    async def fanout_should_not_run(*_args, **_kwargs):
        raise AssertionError("sap_successfactors extract_all should use aggregate DAG")

    monkeypatch.setattr(console_main, "_trigger_airflow_extract_dag", trigger)
    monkeypatch.setattr(console_main, "_record_dag_pipeline_trigger", record)
    monkeypatch.setattr(console_main, "api_pipeline", fanout_should_not_run)

    result = await console_main.api_pipeline_extract_all(
        "sap_successfactors",
        {
            "mode": "incremental",
            "target": "all",
            "conn_id": "femsa_sf",
            "idempotency_key": "studio-extract-all-1",
        },
        user=user,
    )

    assert trigger_calls[0]["dag_id"] == "sap_successfactors_extract_all"
    assert trigger_calls[0]["conf"]["mode"] == "incremental"
    assert trigger_calls[0]["conf"]["target"] == "all"
    assert trigger_calls[0]["conf"]["conn_id"] == "femsa_sf"
    assert trigger_calls[0]["conf"]["tenant_id"] == user["active_tenant_id"]
    assert trigger_calls[0]["conf"]["workspace_id"] == user["active_workspace_id"]
    assert records[0]["entity"] == console_main._SYNC_AGGREGATE_ENTITY
    assert result["trigger_strategy"] == "aggregate_dag"
    assert result["attempted"] == 1
    assert result["summary"]["triggered"] == 1
    assert result["triggered"][0]["entity"] == console_main._SYNC_AGGREGATE_ENTITY


@pytest.mark.anyio
async def test_sync_now_request_id_reuses_existing_terminal_run(
    console_main, monkeypatch
):
    user = _scoped_sf_pipeline_user()
    request_id = "sync-now-ui-retry-1"
    lock_key = console_main._sync_now_lock_key(
        cartridge="sap_successfactors",
        mode="incremental",
        target="all",
        conn_id=None,
        user=user,
    )
    run_id = console_main._sync_now_run_id_from_request_id(
        cartridge="sap_successfactors",
        request_id=request_id,
        lock_key=lock_key,
    )
    existing = {
        "run_id": run_id,
        "dag_id": console_main._SYNC_NOW_DAG_ID,
        "cartridge_id": "sap_successfactors",
        "entity": console_main._SYNC_NOW_ENTITY,
        "mode": "incremental",
        "status": "success",
        "started_at": datetime.now(timezone.utc) - timedelta(minutes=5),
        "finished_at": datetime.now(timezone.utc),
        "error_message": None,
        "extra": {
            "mode": "incremental",
            "target": "all",
            "request_id": request_id,
            "triggered_entities": [{"entity": console_main._SYNC_AGGREGATE_ENTITY}],
            "errors": [],
            "steps": [
                {**step, "status": "success"}
                for step in console_main._initial_sync_steps()
            ],
            "control_room_ready": True,
            "control_room_checked_at": "2026-06-23T21:25:17+00:00",
        },
    }

    async def resolve(user_arg, cartridge_id, fallback=None):
        return "sap_successfactors", True

    async def fetch(*, cartridge, run_id: str, user=None):
        assert cartridge == "sap_successfactors"
        assert run_id == existing["run_id"]
        return dict(existing)

    async def active_should_not_run(**_kwargs):
        raise AssertionError("same request_id should be resolved before active lookup")

    async def upsert_should_not_run(**_kwargs):
        raise AssertionError("same request_id should not create a new sync run")

    async def trigger_should_not_run(*_args, **_kwargs):
        raise AssertionError("same request_id should not trigger Airflow again")

    monkeypatch.setattr(console_main, "_resolve_scoped_operation_cartridge", resolve)
    monkeypatch.setattr(console_main, "_fetch_sync_run", fetch)
    monkeypatch.setattr(console_main, "_fetch_active_sync_run", active_should_not_run)
    monkeypatch.setattr(console_main, "_upsert_sync_run", upsert_should_not_run)
    monkeypatch.setattr(
        console_main, "_trigger_airflow_extract_dag", trigger_should_not_run
    )

    result = await console_main.api_cartridge_sync_now(
        "sap_successfactors",
        {"mode": "incremental", "target": "all", "request_id": request_id},
        user=user,
    )

    assert result["run_id"] == existing["run_id"]
    assert result["status"] == "success"
    assert result["control_room_ready"] is True


@pytest.mark.anyio
async def test_sync_run_get_reconciles_terminal_without_control_room_check(
    console_main, monkeypatch
):
    user = _scoped_sf_pipeline_user()
    row = {
        "run_id": "sync_now:sap_successfactors:terminal-needs-final",
        "dag_id": console_main._SYNC_NOW_DAG_ID,
        "cartridge_id": "sap_successfactors",
        "entity": console_main._SYNC_NOW_ENTITY,
        "mode": "incremental",
        "status": "success",
        "started_at": datetime.now(timezone.utc) - timedelta(minutes=20),
        "finished_at": datetime.now(timezone.utc),
        "error_message": None,
        "extra": {
            "target": "all",
            "mode": "incremental",
            "triggered_entities": [{"entity": console_main._SYNC_AGGREGATE_ENTITY}],
            "errors": [],
            "steps": [
                {**step, "status": "success"}
                for step in console_main._initial_sync_steps()
            ],
            "control_room_ready": False,
        },
    }
    reconciled: list[str] = []

    async def resolve(user_arg, cartridge_id, fallback=None):
        return "sap_successfactors", True

    async def fetch(*, cartridge, run_id, user=None):
        assert cartridge == "sap_successfactors"
        assert run_id == row["run_id"]
        return dict(row)

    async def build_status(*, cartridge, row, user=None):
        reconciled.append(row["run_id"])
        payload = console_main._sync_public_payload(
            row,
            {
                **row["extra"],
                "control_room_ready": True,
                "control_room_checked_at": "2026-06-23T21:30:00+00:00",
            },
        )
        payload["control_room_ready"] = True
        return payload

    monkeypatch.setattr(console_main, "_resolve_scoped_operation_cartridge", resolve)
    monkeypatch.setattr(console_main, "_fetch_sync_run", fetch)
    monkeypatch.setattr(console_main, "_build_sync_run_status", build_status)

    result = await console_main.api_cartridge_sync_run(
        "sap_successfactors",
        row["run_id"],
        user=user,
    )

    assert reconciled == [row["run_id"]]
    assert result["control_room_ready"] is True


@pytest.mark.anyio
async def test_active_sync_run_endpoint_returns_persisted_payload_when_status_build_fails(
    console_main, monkeypatch
):
    user = _scoped_sf_pipeline_user()
    row = {
        "run_id": "sync_now:sap_successfactors:active",
        "dag_id": console_main._SYNC_NOW_DAG_ID,
        "cartridge_id": "sap_successfactors",
        "entity": console_main._SYNC_NOW_ENTITY,
        "mode": "incremental",
        "status": "running",
        "started_at": datetime.now(timezone.utc),
        "finished_at": None,
        "error_message": None,
        "extra": {
            "target": "all",
            "mode": "incremental",
            "steps": console_main._initial_sync_steps(),
            "triggered_entities": [{"entity": console_main._SYNC_AGGREGATE_ENTITY}],
        },
    }

    async def resolve(user_arg, cartridge_id, fallback=None):
        return "sap_successfactors", True

    async def fetch_active(**_kwargs):
        return dict(row)

    async def build_status(**_kwargs):
        raise RuntimeError("pipeline status lookup failed")

    monkeypatch.setattr(console_main, "_resolve_scoped_operation_cartridge", resolve)
    monkeypatch.setattr(console_main, "_fetch_active_sync_run", fetch_active)
    monkeypatch.setattr(console_main, "_build_sync_run_status", build_status)

    result = await console_main.api_cartridge_active_sync_run(
        "sap_successfactors",
        mode="incremental",
        target="all",
        conn_id="femsa_sf",
        user=user,
    )

    assert result["run_id"] == row["run_id"]
    assert result["status"] == "running"
    assert result["target"] == "all"
    assert result["triggered_entities"] == [
        {"entity": console_main._SYNC_AGGREGATE_ENTITY}
    ]


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

    async def build_status(
        *, cartridge, row, user=None, persist_control_room_state=False
    ):
        assert persist_control_room_state is True
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
async def test_build_sync_run_status_runs_agentops_after_successfactors_materializes(
    console_main, monkeypatch
):
    user = _scoped_sf_pipeline_user()
    row = {
        "run_id": "sync_now:sap_successfactors:agentops",
        "dag_id": console_main._SYNC_NOW_DAG_ID,
        "cartridge_id": "sap_successfactors",
        "entity": console_main._SYNC_NOW_ENTITY,
        "mode": "incremental",
        "status": "running",
        "started_at": datetime.now(timezone.utc),
        "finished_at": None,
        "error_message": None,
        "extra": {
            "target": "talent",
            "mode": "incremental",
            "triggered_entities": [
                {"entity": "__extract_all__", "dag_run_id": "child-run"}
            ],
            "errors": [],
            "steps": console_main._initial_sync_steps(),
        },
    }
    upserts: list[dict] = []
    agentops_calls: list[dict] = []

    async def child_runs(**_kwargs):
        return [{"run_id": "child-run", "status": "success"}]

    async def pipeline(cartridge, user=None):
        return {
            "pipeline": [
                {
                    "entity": "EmpCompensation",
                    "bronze": {"status": "fresh"},
                    "silver": [{"name": "sf_empcomp_latest", "status": "fresh"}],
                    "gold": [
                        {"name": "sap_successfactors_talent_signals", "status": "fresh"}
                    ],
                }
            ]
        }

    async def refresh_dashboard_state(user=None):
        return {
            "meta": {"source_count": 3, "item_count": 7},
            "summary": {"total_items": 7, "data_ready_sources": 3},
        }

    async def sf_kpis(user=None):
        return {"row_count": 7}

    control_room_stub = _module(
        refresh_dashboard_state=refresh_dashboard_state,
        sap_successfactors_gold_kpis=sf_kpis,
        sap_successfactors_talent_kpis=sf_kpis,
    )

    async def run_agentops(**kwargs):
        agentops_calls.append(kwargs)
        return {
            "status": "success",
            "total": 1,
            "completed": 1,
            "failed": 0,
            "results": [
                {"agent_slug": "sap_successfactors_talent_monitor", "status": "success"}
            ],
        }

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
    monkeypatch.setattr(console_main, "_run_sync_agentops_monitors", run_agentops)
    monkeypatch.setitem(
        sys.modules, "app.services.control_room_service", control_room_stub
    )
    import app.services as _svc_pkg

    monkeypatch.setattr(
        _svc_pkg, "control_room_service", control_room_stub, raising=False
    )

    result = await console_main._build_sync_run_status(
        cartridge="sap_successfactors",
        row=row,
        user=user,
        persist_control_room_state=True,
    )

    assert agentops_calls == [
        {
            "cartridge": "sap_successfactors",
            "sync_run_id": "sync_now:sap_successfactors:agentops",
            "user": user,
        }
    ]
    assert upserts[-1]["extra"]["agentops_refresh"]["status"] == "success"
    assert any(
        step["id"] == "agents_intelligence" and step["status"] == "success"
        for step in result["steps"]
    )
    control_room_step = next(
        step for step in result["steps"] if step["id"] == "control_room"
    )
    assert control_room_step["status"] == "success"
    assert control_room_step["percent"] == 100
    assert result["progress_percent"] >= 80


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


@pytest.mark.anyio
async def test_successfactors_dag_entity_without_connection_id_returns_400_before_airflow(
    console_main, monkeypatch
):
    async def metadata(cartridge, entity):
        return {
            "pattern": "dag-based",
            "entity": entity,
            "dag_id": "sap_successfactors_extract",
            "mode": "incremental",
            "enabled": True,
            "connection_id": None,
        }

    async def trigger_should_not_run(*_args, **_kwargs):
        raise AssertionError(
            "Airflow should not be triggered without a SuccessFactors conn_id"
        )

    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)
    monkeypatch.setattr(
        console_main, "_trigger_airflow_extract_dag", trigger_should_not_run
    )

    with pytest.raises(HTTPException) as exc:
        await console_main.api_pipeline_extract(
            "sap_successfactors",
            "JobApplication",
            {},
            user=_scoped_sf_pipeline_user(),
        )

    assert exc.value.status_code == 400
    assert "connection_id" in str(exc.value.detail)


@pytest.mark.anyio
async def test_successfactors_entity_extract_reuses_active_run(console_main):
    console_main._test_asyncpg_stub.fetch_rows = [
        {
            "run_id": "manual__user_active",
            "dag_id": "sap_successfactors_extract",
            "entity": "User",
            "airflow_dag_run_id": "manual__user_active",
            "status": "running",
            "mode": "incremental",
            "started_at": datetime.now(timezone.utc),
            "extra": {},
        }
    ]

    result = await console_main._reserve_successfactors_entity_extract_slot(
        cartridge="sap_successfactors",
        entity="User",
        dag_id="sap_successfactors_extract",
        conf={
            "cartridge_id": "sap_successfactors",
            "entity": "User",
            "mode": "incremental",
        },
        user=_scoped_sf_pipeline_user(),
    )

    assert result["response"]["reused"] is True
    assert result["response"]["reason"] == "active_entity_run"
    assert result["response"]["dag_run_id"] == "manual__user_active"
    assert not any(
        "INSERT INTO pipeline_runs" in query
        for query, _args in console_main._test_asyncpg_stub.executed
    )


@pytest.mark.anyio
async def test_successfactors_entity_extract_backpressure_limits_active_runs(
    console_main, monkeypatch
):
    monkeypatch.setattr(
        console_main,
        "_SAP_SUCCESSFACTORS_MAX_ACTIVE_ENTITY_EXTRACTS",
        2,
    )
    console_main._test_asyncpg_stub.fetch_rows = [
        {
            "run_id": "manual__user_active",
            "dag_id": "sap_successfactors_extract",
            "entity": "User",
            "airflow_dag_run_id": "manual__user_active",
            "status": "running",
            "mode": "incremental",
            "started_at": datetime.now(timezone.utc),
            "extra": {},
        },
        {
            "run_id": "manual__phone_active",
            "dag_id": "sap_successfactors_extract",
            "entity": "PerPhone",
            "airflow_dag_run_id": "manual__phone_active",
            "status": "queued",
            "mode": "incremental",
            "started_at": datetime.now(timezone.utc),
            "extra": {},
        },
    ]

    with pytest.raises(HTTPException) as exc:
        await console_main._reserve_successfactors_entity_extract_slot(
            cartridge="sap_successfactors",
            entity="PerEmail",
            dag_id="sap_successfactors_extract",
            conf={
                "cartridge_id": "sap_successfactors",
                "entity": "PerEmail",
                "mode": "incremental",
            },
            user=_scoped_sf_pipeline_user(),
        )

    assert exc.value.status_code == 429
    assert exc.value.detail["reason"] == "too_many_active_entity_extracts"


@pytest.mark.anyio
async def test_successfactors_entity_extract_endpoint_returns_reused_active_run(
    console_main, monkeypatch
):
    async def metadata(cartridge, entity):
        return {
            "pattern": "dag-based",
            "entity": entity,
            "dag_id": "sap_successfactors_extract",
            "mode": "incremental",
            "enabled": True,
            "connection_id": "femsa_sf",
        }

    async def reserve(**_kwargs):
        return {
            "response": {
                "triggered": False,
                "reused": True,
                "cartridge": "sap_successfactors",
                "entity": "User",
                "dag_id": "sap_successfactors_extract",
                "job_id": "manual__user_active",
                "run_id": "manual__user_active",
                "dag_run_id": "manual__user_active",
                "state": "running",
                "reason": "active_entity_run",
                "conf": {},
            }
        }

    async def trigger_should_not_run(*_args, **_kwargs):
        raise AssertionError("Airflow should not be triggered for a reused run")

    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)
    monkeypatch.setattr(
        console_main, "_reserve_successfactors_entity_extract_slot", reserve
    )
    monkeypatch.setattr(
        console_main, "_trigger_airflow_extract_dag", trigger_should_not_run
    )

    result = await console_main.api_pipeline_extract(
        "sap_successfactors",
        "User",
        {},
        user=_scoped_sf_pipeline_user(),
    )

    assert result["triggered"] is False
    assert result["reused"] is True
    assert result["dag_run_id"] == "manual__user_active"


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
    assert department["last_run"]["source"] == "bronze"
    assert department["last_run"]["status"] == "success"
    assert "corrida no registrada" in department["last_run"]["message"]
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
            "run_id": "manual__test",
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
async def test_api_pipeline_entity_runs_formats_successfactors_metadata_block_as_partial(
    console_main, monkeypatch
):
    async def metadata(cartridge, entity):
        return {
            "pattern": "dag-based",
            "entity": entity,
            "dag_id": "sap_successfactors_extract",
            "mode": "incremental",
            "enabled": True,
        }

    console_main._test_asyncpg_stub.fetch_rows = [
        {
            "run_id": "manual__blocked",
            "dag_id": "sap_successfactors_extract",
            "airflow_dag_run_id": "manual__blocked",
            "status": "failed",
            "mode": "incremental",
            "started_at": "2026-06-25T04:09:57+00:00",
            "finished_at": "2026-06-25T04:10:00+00:00",
            "duration_seconds": 3.0,
            "error_message": "HTTP 404",
            "extra": {
                "classification": {
                    "entity": "CareerInterest",
                    "status": "permission-blocked",
                    "code": "SUCCESSFACTORS_METADATA_BLOCKED",
                    "error": "Entity CareerInterest is not found",
                }
            },
        }
    ]
    monkeypatch.setattr(console_main, "_pipeline_extract_metadata", metadata)

    result = await console_main.api_pipeline_entity_runs(
        "sap_successfactors",
        "CareerInterest",
        limit=20,
        user=_scoped_sf_pipeline_user(),
    )

    assert result["runs"][0]["status"] == "partial"
    assert (
        result["runs"][0]["classification"]["code"] == "SUCCESSFACTORS_METADATA_BLOCKED"
    )


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
