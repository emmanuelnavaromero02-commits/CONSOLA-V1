from __future__ import annotations

import asyncio
import importlib
import sys
import threading
import time
import types

import pytest
from fastapi import HTTPException


def _module(**attrs):
    mod = types.ModuleType("stub")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


class _GeneratedSQLValidationError(ValueError):
    pass


class _DuckDBEngineStub:
    pass


@pytest.fixture()
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def refinement_main(monkeypatch):
    monkeypatch.setenv(
        "INTERNAL_API_KEY", "test_internal_api_key_with_more_than_32_chars"
    )
    monkeypatch.setitem(
        sys.modules, "app.duckdb_engine", _module(DuckDBEngine=_DuckDBEngineStub)
    )
    monkeypatch.setitem(
        sys.modules, "app.dataset_store", _module(DatasetStore=lambda path: object())
    )

    async def generate_sql(*args, **kwargs):
        return "", ""

    monkeypatch.setitem(
        sys.modules,
        "app.llm_sql",
        _module(
            GeneratedSQLValidationError=_GeneratedSQLValidationError,
            generate_sql=generate_sql,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "app.security",
        _module(
            get_internal_api_key=lambda: "test_internal_api_key_with_more_than_32_chars"
        ),
    )
    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")
    yield main
    sys.modules.pop("app.main", None)


def test_postgres_dsn_normalizes_sqlalchemy_driver(refinement_main, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://user:pass@host/db")

    assert refinement_main._postgres_dsn() == "postgresql://user:pass@host/db"


def test_postgres_dsn_keeps_native_postgres_url(refinement_main, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")

    assert refinement_main._postgres_dsn() == "postgresql://user:pass@host/db"


def test_dataset_allowed_filters_owned_datasets_for_workspace_employees(
    refinement_main,
):
    base_sec = {
        "trusted": True,
        "source": "console",
        "role": "user",
        "workspace_role": "viewer",
        "user_id": 10,
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "allowed_cartridges": ["hubspot"],
    }
    own_dataset = {
        "name": "forecast_mensual",
        "layer": "gold",
        "cartridge": "hubspot",
        "workspace_id": "workspace-a",
        "created_by_id": 10,
    }
    other_employee_dataset = {
        **own_dataset,
        "name": "deals_estancados",
        "created_by_id": 11,
    }
    legacy_workspace_dataset = {
        **own_dataset,
        "name": "legacy_shared",
        "created_by_id": None,
    }
    workspace_admin_sec = {**base_sec, "workspace_role": "tenant_admin", "user_id": 99}
    wildcard_admin_sec = {**workspace_admin_sec, "allowed_cartridges": ["*"]}

    assert refinement_main._dataset_allowed(base_sec, own_dataset)
    assert not refinement_main._dataset_allowed(base_sec, other_employee_dataset)
    assert refinement_main._dataset_allowed(base_sec, legacy_workspace_dataset)
    assert refinement_main._dataset_allowed(workspace_admin_sec, other_employee_dataset)
    assert refinement_main._dataset_allowed(wildcard_admin_sec, other_employee_dataset)


def test_published_dataset_listing_fails_closed_without_scope(refinement_main):
    assert refinement_main._published_datasets_for_scope({}) == {"datasets": []}


def test_fastapi_api_key_dependency_does_not_consume_worker_pool(refinement_main):
    async_dependency = getattr(refinement_main, "verify_api_key_dependency", None)
    protected_dependencies = []
    for route in refinement_main.app.routes:
        dependant = getattr(route, "dependant", None)
        for dependency in getattr(dependant, "dependencies", []):
            if dependency.call in {refinement_main.verify_api_key, async_dependency}:
                protected_dependencies.append(dependency.call)

    assert async_dependency is not None
    assert asyncio.iscoroutinefunction(async_dependency)
    assert protected_dependencies
    assert all(call is async_dependency for call in protected_dependencies)


@pytest.mark.anyio
async def test_mcp_list_datasets_offloads_blocking_publication_reads(
    refinement_main,
    monkeypatch,
):
    request_thread = threading.get_ident()
    observed = {}
    security_context = {"tenant_id": "tenant", "workspace_id": "workspace"}

    def list_published(sec, annotate_staleness=False):
        observed["thread"] = threading.get_ident()
        observed["sec"] = sec
        observed["annotate_staleness"] = annotate_staleness
        return {"datasets": []}

    monkeypatch.setattr(
        refinement_main,
        "_published_datasets_for_scope",
        list_published,
        raising=False,
    )
    monkeypatch.setattr(
        refinement_main,
        "_require_security_permission",
        lambda *_args: security_context,
    )

    result = await refinement_main.mcp_invoke(
        {"tool": "list_datasets"}, internal_service="console"
    )

    assert result == {"datasets": []}
    assert observed == {
        "thread": observed["thread"],
        "sec": security_context,
        "annotate_staleness": False,
    }
    assert observed["thread"] != request_thread


@pytest.mark.anyio
async def test_mcp_data_catalog_offloads_blocking_publication_reads(
    refinement_main,
    monkeypatch,
):
    request_thread = threading.get_ident()
    observed = {}
    security_context = {"tenant_id": "tenant", "workspace_id": "workspace"}

    def get_data_catalog(*_args, **_kwargs):
        observed["thread"] = threading.get_ident()
        return {"datasets": {}, "relationships": []}

    monkeypatch.setattr(refinement_main, "_get_data_catalog", get_data_catalog)
    monkeypatch.setattr(
        refinement_main,
        "_require_security_permission",
        lambda *_args: security_context,
    )

    result = await refinement_main.mcp_invoke(
        {"tool": "get_data_catalog", "args": {}}, internal_service="console"
    )

    assert result == {"datasets": {}, "relationships": []}
    assert observed["thread"] != request_thread


@pytest.mark.anyio
async def test_rest_list_datasets_offloads_blocking_publication_reads(
    refinement_main,
    monkeypatch,
):
    request_thread = threading.get_ident()
    observed = {}
    security_context = {"tenant_id": "tenant", "workspace_id": "workspace"}

    def list_published(sec, annotate_staleness=False):
        observed["thread"] = threading.get_ident()
        observed["sec"] = sec
        observed["annotate_staleness"] = annotate_staleness
        return {"datasets": []}

    monkeypatch.setattr(
        refinement_main,
        "_published_datasets_for_scope",
        list_published,
        raising=False,
    )
    monkeypatch.setattr(
        refinement_main,
        "_body_from_security_header",
        lambda *_args: {},
    )
    monkeypatch.setattr(
        refinement_main,
        "_require_security_permission",
        lambda *_args: security_context,
    )

    result = await refinement_main.list_datasets(
        x_security_context=None, internal_service="console"
    )

    assert result == {"datasets": []}
    assert observed == {
        "thread": observed["thread"],
        "sec": security_context,
        "annotate_staleness": True,
    }
    assert observed["thread"] != request_thread


@pytest.mark.anyio
async def test_delete_dataset_rejects_invalid_name(refinement_main):
    with pytest.raises(HTTPException) as exc:
        await refinement_main.mcp_invoke(
            {"tool": "delete_dataset", "args": {"name": "bad-name"}}
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid dataset name"


@pytest.mark.anyio
async def test_describe_silver_rejects_invalid_name_before_path_build(refinement_main):
    with pytest.raises(HTTPException) as exc:
        await refinement_main.mcp_invoke(
            {"tool": "describe_silver", "args": {"name": "../secret"}}
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid dataset name"


@pytest.mark.anyio
async def test_mcp_non_string_tool_preserves_unknown_tool_response(refinement_main):
    with pytest.raises(HTTPException) as exc:
        await refinement_main.mcp_invoke(
            {"tool": [], "args": {}}, internal_service="console"
        )

    assert exc.value.status_code == 400
    assert exc.value.detail == "Unknown tool: []"


@pytest.mark.anyio
async def test_rest_dataset_data_endpoint_is_disabled(refinement_main):
    """Regression: GET /datasets/{name}/data used to call query_dataset
    without a user context. Any peer holding INTERNAL_API_KEY (workspace,
    mcp-infra, replicon, airflow) could trigger an RLS-less read of any
    dataset whose SQL did not reference pggold.* . The endpoint is now
    disabled in favour of POST /mcp/invoke with a forwarded user_context."""
    with pytest.raises(HTTPException) as exc:
        await refinement_main.dataset_data("gold_sales", limit=10)

    assert exc.value.status_code == 410
    assert "user_context" in exc.value.detail.lower()


_SYNC_MCP_TOOLS = (
    "list_sources",
    "get_source_partitions",
    "preview_source",
    "preview_transform",
    "save_dataset",
    "delete_dataset",
    "materialize",
    "list_datasets",
    "get_dataset_definition",
    "get_schema",
    "query_dataset",
    "get_lineage",
    "describe_source",
    "describe_silver",
    "list_datasets_with_schemas",
    "get_data_catalog",
    "upsert_catalog_entries",
    "register_relationship",
    "publish_app",
    "list_apps",
    "get_app_details",
    "get_app_html",
    "delete_app",
)


@pytest.mark.anyio
@pytest.mark.parametrize("tool", _SYNC_MCP_TOOLS)
async def test_mcp_sync_tools_delegate_the_complete_operation_to_a_worker(
    refinement_main,
    monkeypatch,
    tool,
):
    request_thread = threading.get_ident()
    observed = {}

    def dispatch(body):
        observed["thread"] = threading.get_ident()
        observed["body"] = body
        return {"handled": body["tool"]}

    monkeypatch.setattr(
        refinement_main,
        "_mcp_invoke_sync",
        dispatch,
        raising=False,
    )

    result = await refinement_main.mcp_invoke(
        {"tool": tool, "args": {"marker": tool}},
        internal_service="console",
    )

    assert result == {"handled": tool}
    assert observed["thread"] != request_thread
    assert observed["body"] == {
        "tool": tool,
        "args": {"marker": tool},
        "_verified_internal_service": "console",
    }


@pytest.mark.anyio
async def test_sync_io_admission_caps_workers_and_keeps_healthz_responsive(
    refinement_main,
    monkeypatch,
):
    release = threading.Event()
    entered_one = threading.Event()
    state_lock = threading.Lock()
    active = 0
    maximum = 0

    def dispatch(body):
        nonlocal active, maximum
        with state_lock:
            active += 1
            maximum = max(maximum, active)
            if active == 1:
                entered_one.set()
        try:
            assert release.wait(5), "blocked sync worker was not released"
            return {"handled": body["args"]["marker"]}
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(refinement_main, "_mcp_invoke_sync", dispatch)
    monkeypatch.setattr(refinement_main, "_list_datasets_sync", dispatch)
    monkeypatch.setattr(
        refinement_main,
        "_body_from_security_header",
        lambda _service, marker: {"args": {"marker": int(marker)}},
    )
    calls = [
        asyncio.create_task(
            refinement_main.mcp_invoke(
                {"tool": "list_sources", "args": {"marker": marker}},
                internal_service="console",
            )
        )
        for marker in range(3)
    ] + [
        asyncio.create_task(
            refinement_main.list_datasets(
                x_security_context=str(marker), internal_service="console"
            )
        )
        for marker in range(3, 6)
    ]

    try:
        assert await asyncio.to_thread(entered_one.wait, 2)
        await asyncio.sleep(0.05)
        started = time.monotonic()
        health = await asyncio.wait_for(refinement_main.healthz(), timeout=0.05)
        elapsed = time.monotonic() - started

        assert health == {"ok": True, "service": "refinement"}
        assert elapsed < 0.05
        assert maximum == 1
    finally:
        release.set()

    assert await asyncio.gather(*calls) == [
        {"handled": marker} for marker in range(6)
    ]
    assert maximum == 1


@pytest.mark.anyio
async def test_total_cap_serializes_long_mutations_and_interactive_reads(
    refinement_main,
    monkeypatch,
):
    releases = {marker: threading.Event() for marker in range(3)}
    entered = {marker: threading.Event() for marker in range(3)}
    state_lock = threading.Lock()
    active = 0
    active_long = 0
    maximum = 0
    maximum_long = 0

    def dispatch(*args):
        nonlocal active, active_long, maximum, maximum_long
        marker = args[-1]["args"]["marker"]
        is_long = marker in {0, 1}
        with state_lock:
            active += 1
            active_long += int(is_long)
            maximum = max(maximum, active)
            maximum_long = max(maximum_long, active_long)
            entered[marker].set()
        try:
            assert releases[marker].wait(5), "blocked sync worker was not released"
            return {"handled": marker}
        finally:
            with state_lock:
                active -= 1
                active_long -= int(is_long)

    monkeypatch.setattr(refinement_main, "_mcp_invoke_sync", dispatch)
    monkeypatch.setattr(refinement_main, "_refresh_dataset_sync", dispatch)
    monkeypatch.setattr(
        refinement_main,
        "_body_from_security_header",
        lambda _service, marker: {"args": {"marker": int(marker)}},
    )
    first_long = asyncio.create_task(
        refinement_main.mcp_invoke(
            {"tool": "materialize", "args": {"marker": 0}},
            internal_service="console",
        )
    )

    try:
        assert await asyncio.to_thread(entered[0].wait, 2)
        second_long = asyncio.create_task(
            refinement_main.refresh_dataset(
                "orders", x_security_context="1", internal_service="console"
            )
        )
        await asyncio.sleep(0)
        interactive = asyncio.create_task(
            refinement_main.mcp_invoke(
                {"tool": "list_sources", "args": {"marker": 2}},
                internal_service="console",
            )
        )
        await asyncio.sleep(0.05)

        assert not entered[1].is_set()
        assert not entered[2].is_set()
        assert maximum_long == 1
        assert maximum == 1

        releases[0].set()
        assert await first_long == {"handled": 0}
        assert await asyncio.to_thread(entered[2].wait, 2)
        assert not entered[1].is_set()
        releases[2].set()
        assert await interactive == {"handled": 2}
        assert await asyncio.to_thread(entered[1].wait, 2)
        releases[1].set()
        assert await second_long == {"handled": 1}
        assert maximum_long == 1
        assert maximum == 1
    finally:
        for release in releases.values():
            release.set()


@pytest.mark.anyio
async def test_cancelled_admitted_request_keeps_its_slot_until_worker_finishes(
    refinement_main,
    monkeypatch,
):
    releases = {marker: threading.Event() for marker in range(3)}
    entered = {marker: threading.Event() for marker in range(3)}
    state_lock = threading.Lock()
    active = 0
    maximum = 0

    def dispatch(body):
        nonlocal active, maximum
        marker = body["args"]["marker"]
        with state_lock:
            active += 1
            maximum = max(maximum, active)
            entered[marker].set()
        try:
            assert releases[marker].wait(5), "blocked sync worker was not released"
            return {"handled": marker}
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(refinement_main, "_mcp_invoke_sync", dispatch)
    first = asyncio.create_task(
        refinement_main.mcp_invoke(
            {"tool": "materialize", "args": {"marker": 0}},
            internal_service="console",
        )
    )
    second = asyncio.create_task(
        refinement_main.mcp_invoke(
            {"tool": "list_sources", "args": {"marker": 1}},
            internal_service="console",
        )
    )

    try:
        assert await asyncio.to_thread(entered[0].wait, 2)
        await asyncio.sleep(0.05)
        assert not entered[1].is_set()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        third = asyncio.create_task(
            refinement_main.mcp_invoke(
                {"tool": "list_sources", "args": {"marker": 2}},
                internal_service="console",
            )
        )
        await asyncio.sleep(0.05)
        assert not entered[1].is_set()
        assert not entered[2].is_set()
        assert maximum == 1

        releases[0].set()
        releases[1].set()
        releases[2].set()
        assert await second == {"handled": 1}
        assert await third == {"handled": 2}
        assert maximum == 1
    finally:
        for release in releases.values():
            release.set()


@pytest.mark.anyio
async def test_readyz_coalesces_concurrent_dependency_checks_and_caches_briefly(
    refinement_main,
    monkeypatch,
):
    release = threading.Event()
    entered = threading.Event()
    state_lock = threading.Lock()
    active = 0
    calls = 0
    maximum = 0

    def readiness_checks():
        nonlocal active, calls, maximum
        with state_lock:
            active += 1
            calls += 1
            maximum = max(maximum, active)
            entered.set()
        try:
            assert release.wait(5), "blocked readiness check was not released"
            return {
                "postgres": "up",
                "duckdb": "up",
                "publication_verifier": "up",
            }
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(refinement_main, "_readiness_checks", readiness_checks)
    assert 0 < refinement_main._READINESS_SUCCESS_CACHE_TTL_SECONDS <= 1
    probes = [asyncio.create_task(refinement_main.readyz()) for _ in range(4)]

    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.sleep(0.05)
        assert await asyncio.wait_for(refinement_main.healthz(), timeout=0.05) == {
            "ok": True,
            "service": "refinement",
        }
        assert calls == 1
        assert maximum == 1
    finally:
        release.set()

    responses = await asyncio.gather(*probes)
    assert [response.status_code for response in responses] == [200, 200, 200, 200]
    assert calls == 1
    assert (await refinement_main.readyz()).status_code == 200
    assert calls == 1

    cached_at, checks = refinement_main._readiness_success_cache
    refinement_main._readiness_success_cache = (
        cached_at - refinement_main._READINESS_SUCCESS_CACHE_TTL_SECONDS - 1,
        checks,
    )
    assert (await refinement_main.readyz()).status_code == 200
    assert calls == 2


@pytest.mark.anyio
async def test_cancelling_readyz_waiter_does_not_cancel_shared_check(
    refinement_main,
    monkeypatch,
):
    release = threading.Event()
    entered = threading.Event()
    calls = 0

    def readiness_checks():
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(5), "blocked readiness check was not released"
        return {
            "postgres": "up",
            "duckdb": "up",
            "publication_verifier": "up",
        }

    monkeypatch.setattr(refinement_main, "_readiness_checks", readiness_checks)
    cancelled_waiter = asyncio.create_task(refinement_main.readyz())

    try:
        assert await asyncio.to_thread(entered.wait, 2)
        surviving_waiter = asyncio.create_task(refinement_main.readyz())
        await asyncio.sleep(0)
        cancelled_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_waiter

        assert calls == 1
        assert not surviving_waiter.done()
        release.set()
        assert (await surviving_waiter).status_code == 200
        assert calls == 1
    finally:
        release.set()


@pytest.mark.anyio
async def test_readyz_failure_is_fail_closed_and_is_not_cached(
    refinement_main,
    monkeypatch,
):
    calls = 0

    def readiness_checks():
        nonlocal calls
        calls += 1
        return {
            "postgres": "up",
            "duckdb": "down",
            "publication_verifier": "up",
        }

    monkeypatch.setattr(refinement_main, "_readiness_checks", readiness_checks)

    first = await refinement_main.readyz()
    second = await refinement_main.readyz()

    assert first.status_code == 503
    assert second.status_code == 503
    assert calls == 2


@pytest.mark.anyio
async def test_readyz_deadline_fails_closed_without_starting_a_second_check(
    refinement_main,
    monkeypatch,
):
    release = threading.Event()
    entered = threading.Event()
    calls = 0

    def readiness_checks():
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(5), "blocked readiness check was not released"
        return {
            "postgres": "up",
            "duckdb": "up",
            "publication_verifier": "up",
        }

    monkeypatch.setattr(refinement_main, "_readiness_checks", readiness_checks)
    monkeypatch.setattr(refinement_main, "_READINESS_TIMEOUT_SECONDS", 0.05)
    assert refinement_main._READINESS_TIMEOUT_SECONDS < 3

    try:
        started = time.monotonic()
        first = await refinement_main.readyz()
        elapsed = time.monotonic() - started
        assert entered.is_set()
        assert first.status_code == 503
        assert elapsed < 0.2

        second = await refinement_main.readyz()
        assert second.status_code == 503
        assert calls == 1
    finally:
        release.set()

    await asyncio.sleep(0.05)
    assert refinement_main._readiness_success_cache is None


@pytest.mark.anyio
async def test_generate_transform_offloads_sync_schema_discovery_only(
    refinement_main,
    monkeypatch,
):
    request_thread = threading.get_ident()
    observed = {}
    context = {"tenant_id": "tenant", "workspace_id": "workspace"}

    def trusted_context(*_args):
        observed["context_thread"] = threading.get_ident()
        return context

    def schema(body, source, ctx):
        observed["schema_thread"] = threading.get_ident()
        observed["schema_args"] = (body, source, ctx)
        return {"fields": [{"name": "id"}]}

    async def generate(description, discovered, *, layer):
        observed["generate_thread"] = threading.get_ident()
        observed["generate_args"] = (description, discovered, layer)
        return "SELECT 1", "ok"

    monkeypatch.setattr(refinement_main, "_schema_for_transform_source", schema)
    monkeypatch.setattr(refinement_main, "generate_sql", generate)
    monkeypatch.setattr(
        refinement_main,
        "_trusted_user_context",
        trusted_context,
    )
    body = {
        "tool": "generate_transform",
        "args": {
            "description": "orders",
            "sources": ["raw/example/orders"],
            "layer": "silver",
        },
    }

    result = await refinement_main.mcp_invoke(body, internal_service="console")

    assert result == {
        "sql": "SELECT 1",
        "explanation": "ok",
        "cartridge": None,
        "layer": "silver",
    }
    assert observed["context_thread"] != request_thread
    assert observed["schema_thread"] != request_thread
    assert observed["generate_thread"] == request_thread
    assert observed["generate_args"] == (
        "orders",
        {"raw/example/orders": {"fields": [{"name": "id"}]}},
        "silver",
    )


@pytest.mark.anyio
@pytest.mark.parametrize("entrypoint", ("mcp", "rest", "source"))
async def test_materialize_finalize_chain_stays_in_one_worker_thread(
    refinement_main,
    monkeypatch,
    entrypoint,
):
    request_thread = threading.get_ident()
    local = threading.local()
    events = []
    security_context = {"tenant_id": "tenant", "workspace_id": "workspace"}
    dataset = {
        "name": "orders",
        "layer": "silver",
        "sources": ["raw/example/orders"],
    }

    class Store:
        def list_datasets(self, **scope):
            events.append(("list", threading.get_ident(), scope))
            return [dataset]

        def get_dataset(self, name, **scope):
            events.append(("get", threading.get_ident(), name, scope))
            return dataset

        def update_refresh(self, name, row_count, **scope):
            assert local.publication_replay is False
            events.append(("refresh", threading.get_ident(), name, row_count, scope))

    class Engine:
        def missing_materialized_dependencies(self, sources, ctx):
            events.append(("dependencies", threading.get_ident(), sources, ctx))
            return []

        def materialize(self, ds, ctx):
            local.materialized = True
            events.append(("materialize", threading.get_ident(), ds, ctx))
            return {"row_count": 3}

        def consume_publication_replay(self):
            assert local.materialized is True
            local.publication_replay = False
            events.append(("replay", threading.get_ident()))
            return False

    def reindex(name, _body):
        assert local.publication_replay is False
        events.append(("reindex", threading.get_ident(), name))

    monkeypatch.setattr(refinement_main, "store", Store())
    monkeypatch.setattr(refinement_main, "engine", Engine())
    monkeypatch.setattr(
        refinement_main,
        "_require_security_permission",
        lambda *_args: security_context,
    )
    monkeypatch.setattr(refinement_main, "_require_source_scope", lambda *_args: None)
    monkeypatch.setattr(
        refinement_main,
        "_trusted_user_context",
        lambda *_args: {"workspace_id": "workspace"},
    )
    monkeypatch.setattr(refinement_main, "_dataset_allowed", lambda *_args: True)
    monkeypatch.setattr(refinement_main, "_reindex_dataset_best_effort", reindex)

    monkeypatch.setattr(
        refinement_main,
        "_materialize_with_operational_fallback",
        refinement_main.engine.materialize,
    )

    if entrypoint == "mcp":
        result = await refinement_main.mcp_invoke(
            {"tool": "materialize", "args": {"name": "orders"}},
            internal_service="console",
        )
    elif entrypoint == "rest":
        result = await refinement_main.refresh_dataset(
            "orders", x_security_context=None, internal_service="console"
        )
    else:
        result = await refinement_main.refresh_by_source(
            {"source": "raw/example/orders"}, internal_service="console"
        )

    if entrypoint == "source":
        assert result["status"] == "success"
        assert result["refreshed"] == 1
        expected = [
            "list",
            "get",
            "dependencies",
            "materialize",
            "replay",
            "refresh",
            "reindex",
        ]
    else:
        assert result == {"row_count": 3}
        expected = ["get", "materialize", "replay", "refresh", "reindex"]
    assert [event[0] for event in events] == expected
    worker_threads = {event[1] for event in events}
    assert len(worker_threads) == 1
    assert worker_threads != {request_thread}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "helper", "args", "kwargs"),
    (
        (
            "list_datasets",
            "_list_datasets_sync",
            (),
            {"x_security_context": None, "internal_service": "console"},
        ),
        (
            "dataset_definition",
            "_dataset_definition_sync",
            ("orders",),
            {"x_security_context": None, "internal_service": "console"},
        ),
        (
            "dataset_schema",
            "_dataset_schema_sync",
            ("orders",),
            {"x_security_context": None, "internal_service": "console"},
        ),
        (
            "refresh_dataset",
            "_refresh_dataset_sync",
            ("orders",),
            {"x_security_context": None, "internal_service": "console"},
        ),
        (
            "refresh_by_source",
            "_refresh_by_source_sync",
            ({"source": "raw/example/orders"},),
            {"internal_service": "console"},
        ),
    ),
)
async def test_rest_dataset_io_routes_delegate_to_worker(
    refinement_main,
    monkeypatch,
    endpoint,
    helper,
    args,
    kwargs,
):
    request_thread = threading.get_ident()
    observed = {}

    def run_sync(*helper_args):
        observed["thread"] = threading.get_ident()
        observed["args"] = helper_args
        return {"route": endpoint}

    monkeypatch.setattr(refinement_main, helper, run_sync, raising=False)

    result = await getattr(refinement_main, endpoint)(*args, **kwargs)

    assert result == {"route": endpoint}
    assert observed["thread"] != request_thread
