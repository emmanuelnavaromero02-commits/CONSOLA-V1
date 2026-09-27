from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from app.dependencies import require_authenticated
from app.routers import control_room as routes
from app.routers import control_room_actions as actions_router
from app.schemas.control_room_live import ControlRoomRefreshResponse
from app.services import control_room_service
from app.services.control_room import business_state_refresh as refresh_service
from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.control_room.business_item_persistence_sql import (
    LAPSE_EXCEPTIONS_SQL,
    PERSIST_ITEMS_SQL,
)
from app.services.control_room.business_state_persistence import (
    persist_refresh_items,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    persistence_metadata,
)
from app.services.csrf import require_csrf
from app.services.rate_limiter import InMemoryRateLimiter
from app.services.request_rate_limits import (
    RATE_LIMITS,
    rate_limit_authenticated_action,
)
from control_room_surface_fixtures import OPERATOR, VIEWER, business_item


PATH = "/api/control-room/refresh"
ROUTERS = Path(__file__).resolve().parents[1] / "app" / "routers"


def _route():
    return next(route for route in routes.router.routes if route.path == PATH)


def test_refresh_is_one_csrf_protected_write_scoped_post():
    route = _route()
    names = {
        getattr(dependency.dependency, "required_permission", None)
        or dependency.dependency.__name__
        for dependency in route.dependencies
    }
    assert route.methods == {"POST"}
    assert names == {"require_csrf", "control_room.write"}
    assert route.response_model is ControlRoomRefreshResponse
    assert RATE_LIMITS[PATH] == (6, 60)
    assert RATE_LIMITS[f"{PATH}:workspace"] == (30, 60)


def test_only_the_explicit_refresh_route_writes_dashboard_state_over_http():
    writers = sorted(
        path.name
        for path in ROUTERS.glob("control_room*.py")
        if "refresh_control_room_state" in path.read_text(encoding="utf-8")
        or "refresh_dashboard_state" in path.read_text(encoding="utf-8")
    )
    assert writers == ["control_room_actions.py"]
    for route in routes.router.routes:
        if "GET" in (getattr(route, "methods", None) or set()):
            names = route.endpoint.__code__.co_names
            assert "refresh_control_room_state" not in names
            assert "refresh_dashboard_state" not in names


def _client(user: dict | None, *, csrf: bool = True) -> httpx.AsyncClient:
    app = FastAPI()
    if user is not None:

        @app.middleware("http")
        async def _inject(request: Request, call_next):
            request.state.user = user
            return await call_next(request)

        app.dependency_overrides[require_authenticated] = lambda: user
    if csrf:
        app.dependency_overrides[require_csrf] = lambda: None
    app.include_router(routes.router)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_refresh_requires_authentication_write_and_csrf():
    refresh = AsyncMock(side_effect=AssertionError("refreshed"))
    with patch.object(actions_router, "refresh_control_room_state", new=refresh):
        async with _client(None) as client:
            assert (await client.post(PATH)).status_code == 401
        async with _client(VIEWER) as client:
            assert (await client.post(PATH)).status_code == 403
        async with _client(OPERATOR, csrf=False) as client:
            assert (await client.post(PATH)).status_code == 403
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_is_rate_limited_per_user_before_touching_state():
    refresh = AsyncMock(side_effect=AssertionError("refreshed"))
    limiter = AsyncMock(side_effect=HTTPException(429, "too many requests"))
    with (
        patch.object(actions_router, "refresh_control_room_state", new=refresh),
        patch.object(actions_router, "rate_limit_authenticated_action", new=limiter),
    ):
        async with _client(OPERATOR) as client:
            response = await client.post(PATH)

    assert response.status_code == 429
    assert limiter.await_args.args == (PATH,)
    assert limiter.await_args.kwargs == {
        "user_id": 9,
        "workspace_id": OPERATOR["active_workspace_id"],
    }
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_limits_are_per_user_and_per_workspace_never_per_ip(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    limiter = InMemoryRateLimiter()

    async def hit(user_id: int, workspace: str) -> int:
        try:
            await rate_limit_authenticated_action(
                PATH,
                user_id=user_id,
                workspace_id=workspace,
                limiter_factory=lambda: limiter,
            )
        except HTTPException as exc:
            return exc.status_code
        return 200

    assert [await hit(1, "ws-a") for _ in range(7)] == [200] * 6 + [429]
    assert await hit(2, "ws-a") == 200
    for user_id in range(3, 26):
        assert await hit(user_id, "ws-a") == 200
    assert await hit(99, "ws-a") == 429
    assert await hit(99, "ws-b") == 200
    assert not any(":-" in key or "127.0.0.1" in key for key in limiter._buckets)
    with pytest.raises(HTTPException) as missing:
        await rate_limit_authenticated_action(
            PATH, user_id=None, workspace_id="ws-a", limiter_factory=lambda: limiter
        )
    assert missing.value.status_code == 403


class ScopedConn:
    def __init__(self) -> None:
        self.statements: list[str] = []

    @asynccontextmanager
    async def transaction(self):
        yield

    def is_in_transaction(self) -> bool:
        return False

    async def execute(self, query: str, *_args: Any) -> str:
        self.statements.append(" ".join(query.split()))
        return "SELECT 1"


@pytest.mark.asyncio
async def test_refresh_audits_inside_the_persistence_transaction():
    conn = ScopedConn()
    audit = AsyncMock()

    async def persisted(_user, *, on_persisted):
        await on_persisted(conn, 5, 2)
        return {"items": []}

    with (
        patch.object(control_room_service, "refresh_dashboard_state", new=persisted),
        patch.object(refresh_service.audit_service, "record_event", new=audit),
        patch.object(
            refresh_service.auth, "pool", new=AsyncMock(side_effect=AssertionError)
        ),
    ):
        await refresh_service.refresh_control_room_state(OPERATOR)

    kwargs = audit.await_args.kwargs
    assert audit.await_count == 1
    assert kwargs["connection"] is conn
    assert kwargs["metadata"] == {"persisted_rows": 5, "lapsed_exceptions": 2}
    assert kwargs["critical"] is True


@pytest.mark.asyncio
async def test_audit_failure_inside_the_transaction_fails_the_refresh():
    async def persisted(_user, *, on_persisted):
        await on_persisted(ScopedConn(), 1, 0)
        return {"items": []}

    with (
        patch.object(control_room_service, "refresh_dashboard_state", new=persisted),
        patch.object(
            refresh_service.audit_service,
            "record_event",
            new=AsyncMock(side_effect=RuntimeError("audit table missing")),
        ),
    ):
        with pytest.raises(RuntimeError):
            await refresh_service.refresh_control_room_state(OPERATOR)


@pytest.mark.asyncio
async def test_refresh_persists_audits_and_invalidates_the_read_cache():
    persisted = AsyncMock(return_value={"items": [{"id": "a"}, {"id": "b"}]})
    audit = AsyncMock()
    conn = ScopedConn()
    with (
        patch.object(control_room_service, "refresh_dashboard_state", new=persisted),
        patch.object(refresh_service.audit_service, "record_event", new=audit),
        patch.object(refresh_service.auth, "pool", new=AsyncMock(return_value=conn)),
        patch.object(actions_router, "_control_room_cache_invalidate") as invalidate,
    ):
        async with _client(OPERATOR) as client:
            response = await client.post(PATH, headers={"user-agent": "pytest"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "refreshed_at", "message"}
    assert body["status"] == "refreshed"
    persisted.assert_awaited_once()
    kwargs = audit.await_args.kwargs
    assert kwargs["connection"] is conn
    assert conn.statements[0].upper().startswith("SELECT SET_CONFIG")
    assert kwargs["action"] == "control_room.state.refresh"
    assert kwargs["critical"] is True
    assert kwargs["metadata"] == {"persisted_rows": 0, "lapsed_exceptions": 0}
    assert "on_persisted" in persisted.await_args.kwargs
    assert kwargs["user_agent"] == "pytest"
    invalidate.assert_called_once()


@pytest.mark.asyncio
async def test_refresh_failure_is_not_audited_as_success():
    audit = AsyncMock()
    with (
        patch.object(
            control_room_service,
            "refresh_dashboard_state",
            new=AsyncMock(side_effect=RuntimeError("database down")),
        ),
        patch.object(refresh_service.audit_service, "record_event", new=audit),
    ):
        with pytest.raises(RuntimeError):
            await refresh_service.refresh_control_room_state(OPERATOR)
    audit.assert_not_awaited()
    with pytest.raises(HTTPException):
        await refresh_service.refresh_control_room_state(VIEWER)


def test_refresh_response_model_is_strict():
    with pytest.raises(ValidationError):
        ControlRoomRefreshResponse.model_validate(
            {"status": "refreshed", "refreshed_at": "2026-09-26T00:00:00Z", "items": 3}
        )


class PersistConn:
    def __init__(self, existing: list[dict[str, Any]] | None = None) -> None:
        self.existing = existing or []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: Any):
        statement = " ".join(query.split())
        self.calls.append((statement, args))
        if "FROM requested r" in statement:
            return [dict(row) for row in self.existing]
        if "owner_user_id IS DISTINCT FROM" in statement:
            return [
                {"item_id": row["item_id"]}
                for row in self.existing
                if row.get("owner_user_id") != args[2]
            ]
        return []

    async def execute(self, query: str, *args: Any) -> str:
        statement = " ".join(query.split())
        self.calls.append((statement, args))
        if query == PERSIST_ITEMS_SQL:
            return f"INSERT 0 {len(json.loads(args[0]))}"
        if query == LAPSE_EXCEPTIONS_SQL:
            return f"INSERT 0 {len(args[1])}"
        return "INSERT 0 0"

    def executed(self, sql: str) -> list[tuple[Any, ...]]:
        target = " ".join(sql.split())
        return [args for statement, args in self.calls if statement == target]


def _prepared_row(item: dict[str, Any], fingerprint: str) -> dict[str, Any]:
    return {
        "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "workspace_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "owner_user_id": 9,
        "item_id": item["id"],
        "id": item["id"],
        "item_kind": "anomaly",
        "kind": "anomaly",
        "cartridge_id": "sap_hcm",
        "source_dataset": "gold_business_observations",
        "title": item["title"],
        "status": "open",
        "metric_type": "count",
        "observed_value": item["observed_value"],
        "population_count": 10,
        "observation_date": "2026-09-20",
        "metadata": {CURRENT_ELIGIBILITY_FINGERPRINT_KEY: fingerprint},
    }


def _existing_exception(item_id: str, resolution_fingerprint: str) -> dict[str, Any]:
    return {
        "workspace_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "item_id": item_id,
        "owner_user_id": 9,
        "status": "dismissed",
        "decision_id": None,
        "selected_option_id": None,
        "execution_status": "not_started",
        "metadata": {
            "resolution": "exception_approved",
            "resolution_observation_fingerprint": resolution_fingerprint,
        },
    }


@pytest.mark.asyncio
async def test_a_new_observation_lapses_the_approved_exception_in_the_same_batch():
    item = business_item("business-1")
    conn = PersistConn([_existing_exception("business-1", "a" * 64)])
    rows = [_prepared_row(item, "b" * 64)]

    lapsed = await persist_item_rows(conn, rows, owner_scope_id=9)

    assert lapsed == 1
    lapses = conn.executed(LAPSE_EXCEPTIONS_SQL)
    assert lapses == [("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", ["business-1"], 9)]
    statements = [statement for statement, _ in conn.calls]
    assert statements.index(" ".join(LAPSE_EXCEPTIONS_SQL.split())) > statements.index(
        " ".join(PERSIST_ITEMS_SQL.split())
    )


@pytest.mark.asyncio
async def test_the_same_observation_keeps_the_exception_and_no_lapse_runs():
    item = business_item("business-1")
    row = _prepared_row(item, "")
    fingerprint = persistence_metadata(row)[CURRENT_ELIGIBILITY_FINGERPRINT_KEY]
    conn = PersistConn([_existing_exception("business-1", fingerprint)])

    await persist_item_rows(conn, [row], workspace_wide=True)

    assert conn.executed(LAPSE_EXCEPTIONS_SQL) == []


def test_lapse_sql_reopens_only_unlinked_approved_exceptions_and_records_an_event():
    sql = " ".join(LAPSE_EXCEPTIONS_SQL.split())
    assert "SET status = 'open', dismissed_at = NULL" in sql
    assert "status = 'dismissed'" in sql
    assert "metadata->>'resolution' = 'exception_approved'" in sql
    assert "decision_id IS NULL" in sql
    assert (
        "(metadata->>'resolution_observation_fingerprint') IS DISTINCT FROM "
        "(metadata->>'business_eligibility_fingerprint')"
    ) in sql
    for key in (
        "resolution",
        "resolution_actor_id",
        "resolution_reason",
        "resolution_at",
        "resolution_observation_fingerprint",
        "resolution_evidence_digest",
    ):
        assert f"- '{key}'" in sql
    assert "'exception_lapsed'" in sql
    assert "INSERT INTO control_room_item_events" in sql


@pytest.mark.asyncio
async def test_owner_scoped_refresh_skips_rows_owned_by_someone_else():
    mine = business_item("business-1")
    theirs = business_item("business-2")
    conn = PersistConn(
        [
            {"item_id": "business-2", "owner_user_id": 44},
            {"item_id": "business-3", "owner_user_id": 9},
        ]
    )

    async def _pool():
        return conn

    async def _scoped(pool, user, work):
        return await work(
            pool,
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        )

    await persist_refresh_items(
        [mine, theirs],
        user={**OPERATOR, "role": "analyst"},
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        workspace_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        actor_id=9,
        workspace_wide=False,
        pool_factory=_pool,
        run_scoped=_scoped,
        impact_builder=lambda *_args, **_kwargs: {"estimate": 1},
        metadata_builder=lambda item, _impact: {},
        diagnostic_builder=lambda item: {},
    )

    (persisted,) = conn.executed(PERSIST_ITEMS_SQL)
    assert [row["item_id"] for row in json.loads(persisted[0])] == ["business-1"]


@pytest.mark.asyncio
async def test_persistence_hook_runs_on_the_same_connection_with_counts():
    mine = business_item("business-1")
    theirs = business_item("business-2")
    calls: list[tuple[Any, int, int]] = []

    async def hook(conn: Any, persisted: int, lapsed: int) -> None:
        calls.append((conn, persisted, lapsed))

    for existing, expected in (
        ([{"item_id": "business-2", "owner_user_id": 44}], 1),
        (
            [
                {"item_id": "business-1", "owner_user_id": 44},
                {"item_id": "business-2", "owner_user_id": 44},
            ],
            0,
        ),
    ):
        conn = PersistConn(existing)

        async def _pool(conn=conn):
            return conn

        async def _scoped(pool, user, work):
            return await work(
                pool,
                "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            )

        await persist_refresh_items(
            [mine, theirs],
            user={**OPERATOR, "role": "analyst"},
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            workspace_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            actor_id=9,
            workspace_wide=False,
            pool_factory=_pool,
            run_scoped=_scoped,
            impact_builder=lambda *_args, **_kwargs: {"estimate": 1},
            metadata_builder=lambda item, _impact: {},
            diagnostic_builder=lambda item: {},
            on_persisted=hook,
        )
        assert calls[-1] == (conn, expected, 0)


@pytest.mark.asyncio
async def test_platform_admin_without_an_active_workspace_is_refused_before_any_read(
    monkeypatch,
):
    admin = {"id": 1, "role": "super_admin", "email": "root@example.test"}
    persisted = AsyncMock(side_effect=AssertionError("dashboard state touched"))
    with (
        patch.object(control_room_service, "refresh_dashboard_state", new=persisted),
        patch.object(
            refresh_service.auth, "pool", new=AsyncMock(side_effect=AssertionError)
        ),
    ):
        async with _client(admin) as client:
            response = await client.post(PATH)
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
        with pytest.raises(HTTPException) as refused:
            await rate_limit_authenticated_action(
                PATH,
                user_id=admin["id"],
                workspace_id=None,
                limiter_factory=lambda: InMemoryRateLimiter(),
            )

    assert response.status_code == 403
    assert "item" not in response.text
    persisted.assert_not_awaited()
    assert refused.value.status_code == 403
