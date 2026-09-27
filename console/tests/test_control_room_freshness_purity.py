from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, get_args, get_origin
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, Request
from pydantic import ValidationError

from app.dependencies import require_authenticated
from app.routers import control_room as routes
from app.routers import control_room_surfaces as surfaces
from app.schemas.control_room_live import ControlRoomFreshnessResponse
from app.services import control_room_service
from app.services.control_room import business_action_binding_producer as producer
from app.services.control_room import experience_freshness as freshness
from control_room_get_edges import installed_read_edges
from control_room_get_harness import (
    TENANT_ID,
    WORKSPACE_ID,
    ConcurrencyProbe,
    MutationSentinel,
    build_app,
)


PATH = "/api/control-room/experience/v2/freshness"
WRITER = {
    "id": 9,
    "role": "tenant_admin",
    "active_tenant_id": TENANT_ID,
    "active_workspace_id": WORKSPACE_ID,
    "allowed_cartridges": ["sap_hcm"],
}
VIEWER = {**WRITER, "id": 7, "role": "viewer"}
_DML = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|MERGE|CALL|TRUNCATE|CREATE|ALTER|DROP|COPY|LOCK)\b"
    r"|FOR\s+(?:UPDATE|SHARE|NO\s+KEY|KEY)",
    re.I,
)
REFRESHED = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


class FreshnessConn:
    def __init__(self, **overrides: Any) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.data: dict[str, Any] = {
            "items": [
                {
                    "status": "open",
                    "item_count": 3,
                    "last_seen_at": datetime(2026, 9, 25, 8, tzinfo=UTC),
                },
                {
                    "status": "dismissed",
                    "item_count": 1,
                    "last_seen_at": datetime(2026, 9, 24, 8, tzinfo=UTC),
                },
            ],
            "events": {"last_event_id": 41, "event_count": 12},
            "thresholds": {"updated_at": None, "threshold_count": 0},
            "installations": {
                "updated_at": datetime(2026, 9, 1, tzinfo=UTC),
                "installation_count": 1,
            },
            "gold": [
                {
                    "name": "gold_business_observations",
                    "cartridge": "sap_hcm",
                    "last_refresh": REFRESHED,
                    "row_count": 120,
                    "updated_at": REFRESHED,
                }
            ],
            "templates": [
                {
                    "template_id": "approve_exception",
                    "cartridge_id": "platform",
                    "label": "Aprobar Excepción",
                    "requires_approval": False,
                }
            ],
        }
        self.data.update(overrides)

    def _record(self, query: str, args: tuple[Any, ...]) -> str:
        statement = " ".join(str(query).split())
        assert not _DML.search(statement), statement
        assert "control_room_action_tokens" not in statement
        self.statements.append((statement, args))
        return statement

    async def execute(self, query: str, *args: Any) -> str:
        self._record(query, args)
        return "SELECT 1"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        statement = self._record(query, args)
        if "FROM control_room_items" in statement:
            return [dict(row) for row in self.data["items"]]
        if "FROM datasets" in statement:
            return [dict(row) for row in self.data["gold"]]
        if "FROM control_room_action_templates" in statement:
            return [dict(row) for row in self.data["templates"]]
        raise AssertionError(f"unexpected fetch: {statement}")

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        statement = self._record(query, args)
        if "FROM control_room_item_events" in statement:
            return dict(self.data["events"])
        if "FROM control_room_thresholds" in statement:
            return dict(self.data["thresholds"])
        if "FROM cartridge_installations" in statement:
            return dict(self.data["installations"])
        raise AssertionError(f"unexpected fetchrow: {statement}")


async def _fingerprint(conn: FreshnessConn, user: dict[str, Any] = WRITER):
    with patch.object(freshness.auth, "pool", new=AsyncMock(return_value=conn)):
        return await freshness.compute_experience_fingerprint(user)


@pytest.mark.asyncio
async def test_writer_freshness_reads_only_inside_a_read_only_transaction():
    conn = FreshnessConn()
    issue = AsyncMock(side_effect=AssertionError("binding slots touched"))
    collect = AsyncMock(side_effect=AssertionError("surface snapshot built"))
    with (
        patch.object(producer, "issue_action_bindings", issue),
        patch.object(surfaces, "issue_action_bindings", issue),
        patch.object(surfaces, "collect_surface_snapshot", collect),
    ):
        response = await _fingerprint(conn)

    statements = [statement for statement, _args in conn.statements]
    assert statements[0].upper().startswith("SELECT SET_CONFIG")
    assert statements[1] == freshness.READ_ONLY_TRANSACTION_SQL
    assert all(
        statement.upper().startswith(("SELECT", "SET TRANSACTION READ ONLY"))
        for statement in statements
    )
    assert any("FROM control_room_action_templates" in s for s in statements)
    assert not any("control_room_action_tokens" in s for s in statements)
    issue.assert_not_awaited()
    collect.assert_not_awaited()
    assert re.fullmatch(r"[a-f0-9]{64}", response.fingerprint)
    assert response.data_refreshed_at == REFRESHED
    assert response.schema_version == "control-room-freshness/v1"


@pytest.mark.asyncio
async def test_fingerprint_is_stable_for_unchanged_inputs():
    first = await _fingerprint(FreshnessConn())
    second = await _fingerprint(FreshnessConn())

    assert first.fingerprint == second.fingerprint
    assert second.checked_at >= first.checked_at


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    (
        {"events": {"last_event_id": 42, "event_count": 13}},
        {
            "gold": [
                {
                    "name": "gold_business_observations",
                    "cartridge": "sap_hcm",
                    "last_refresh": datetime(2026, 9, 26, 6, tzinfo=UTC),
                    "row_count": 121,
                    "updated_at": datetime(2026, 9, 26, 6, tzinfo=UTC),
                }
            ]
        },
        {
            "items": [
                {
                    "status": "dismissed",
                    "item_count": 4,
                    "last_seen_at": datetime(2026, 9, 25, 8, tzinfo=UTC),
                }
            ]
        },
        {"thresholds": {"updated_at": REFRESHED, "threshold_count": 1}},
        {"installations": {"updated_at": REFRESHED, "installation_count": 2}},
        {"templates": []},
    ),
)
async def test_fingerprint_moves_when_an_observed_input_changes(overrides):
    baseline = await _fingerprint(FreshnessConn())
    changed = await _fingerprint(FreshnessConn(**overrides))

    assert changed.fingerprint != baseline.fingerprint


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user",
    (
        {**WRITER, "role": "workspace_admin"},
        {**WRITER, "allowed_cartridges": ["sap_hcm", "sap_b1"]},
        {**WRITER, "id": 10},
        {**WRITER, "access_revision": "rev-2"},
    ),
)
async def test_fingerprint_moves_when_authorization_changes(user):
    baseline = await _fingerprint(FreshnessConn())
    changed = await _fingerprint(FreshnessConn(), user)

    assert changed.fingerprint != baseline.fingerprint


@pytest.mark.asyncio
async def test_reader_never_reads_the_action_catalog_and_is_owner_scoped():
    conn = FreshnessConn()
    await _fingerprint(conn, {**VIEWER, "role": "analyst"})

    statements = dict(conn.statements)
    assert not any("control_room_action_templates" in s for s in statements)
    item_args = next(
        args for statement, args in conn.statements if "GROUP BY status" in statement
    )
    assert item_args == (WORKSPACE_ID, TENANT_ID, 7)


@pytest.mark.asyncio
async def test_restricted_user_without_actor_id_sees_no_rows_instead_of_all():
    conn = FreshnessConn()
    await _fingerprint(conn, {**VIEWER, "id": None})

    item_args = next(
        args for statement, args in conn.statements if "GROUP BY status" in statement
    )
    assert item_args[2] == 0


@pytest.mark.asyncio
async def test_workspace_wide_reader_is_not_owner_filtered_and_cartridges_bound():
    conn = FreshnessConn()
    await _fingerprint(conn)

    item_args = next(
        args for statement, args in conn.statements if "GROUP BY status" in statement
    )
    gold_args = next(
        args for statement, args in conn.statements if "FROM datasets" in statement
    )
    assert item_args[2] is None
    assert gold_args[1] == ["sap_hcm"]
    assert gold_args[2] == freshness.MAX_GOLD_DATASETS


@pytest.mark.asyncio
async def test_empty_cartridge_scope_skips_gold_and_reports_no_refresh():
    conn = FreshnessConn()
    response = await _fingerprint(conn, {**WRITER, "allowed_cartridges": []})

    assert not any("FROM datasets" in s for s, _ in conn.statements)
    assert response.data_refreshed_at is None


def test_freshness_response_model_is_strict_and_free_of_open_types():
    payload = {
        "schema_version": "control-room-freshness/v1",
        "fingerprint": "a" * 64,
        "checked_at": datetime(2026, 9, 26, tzinfo=UTC),
        "data_refreshed_at": None,
    }
    ControlRoomFreshnessResponse.model_validate(payload)
    for invalid in (
        {**payload, "fingerprint": "A" * 64},
        {**payload, "fingerprint": "a" * 63},
        {**payload, "schema_version": "control-room-freshness/v2"},
        {**payload, "item_ids": ["business-1"]},
    ):
        with pytest.raises(ValidationError):
            ControlRoomFreshnessResponse.model_validate(invalid)
    assert ControlRoomFreshnessResponse.model_config.get("extra") == "forbid"
    for field in ControlRoomFreshnessResponse.model_fields.values():
        nodes = [field.annotation, *get_args(field.annotation)]
        assert Any not in nodes
        assert all(get_origin(node) is not dict for node in nodes)


@pytest.mark.asyncio
async def test_asgi_freshness_is_repeatable_scoped_and_dml_free():
    sentinel = MutationSentinel()
    before = sentinel.snapshot()
    app = build_app(ConcurrencyProbe())
    transport = httpx.ASGITransport(app=app)
    with installed_read_edges(sentinel, ConcurrencyProbe()):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            first = await client.get(PATH, headers={"x-purity-request": "fresh-a"})
            second = await client.get(PATH, headers={"x-purity-request": "fresh-b"})

    assert first.status_code == second.status_code == 200
    assert first.json()["fingerprint"] == second.json()["fingerprint"]
    assert set(first.json()) == {
        "schema_version",
        "fingerprint",
        "checked_at",
        "data_refreshed_at",
    }
    assert sentinel.snapshot() == before
    assert sentinel.mutation_attempts == []
    statements = [statement for _request, statement, _scope in sentinel.query_calls]
    assert statements
    assert statements[0] == freshness.READ_ONLY_TRANSACTION_SQL
    assert not any("control_room_action_tokens" in s for s in statements)
    assert all(
        scope == (TENANT_ID, WORKSPACE_ID) for _r, _s, scope in sentinel.query_calls
    )


def _client(user: dict | None) -> httpx.AsyncClient:
    app = FastAPI()
    if user is not None:

        @app.middleware("http")
        async def _inject(request: Request, call_next):
            request.state.user = user
            return await call_next(request)

        app.dependency_overrides[require_authenticated] = lambda: user
    app.include_router(routes.router)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_freshness_requires_authentication_and_dataset_read():
    compute = AsyncMock(side_effect=AssertionError("fingerprint computed"))
    with patch.object(control_room_service.auth, "pool", compute):
        async with _client(None) as client:
            assert (await client.get(PATH)).status_code == 401
        async with _client({**WRITER, "role": "workspace_user"}) as client:
            assert (await client.get(PATH)).status_code == 403
    compute.assert_not_awaited()


def test_freshness_route_is_get_only_and_declares_dataset_read():
    matches = [route for route in routes.router.routes if route.path == PATH]
    assert len(matches) == 1
    route = matches[0]
    permissions = {
        dependency.dependency.required_permission
        for dependency in route.dependencies
        if hasattr(dependency.dependency, "required_permission")
    }
    assert route.methods == {"GET"}
    assert permissions == {"datasets.read"}
    assert route.response_model is ControlRoomFreshnessResponse
