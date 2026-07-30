from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.dependencies import require_authenticated
from app.routers import control_room
from app.routers import control_room_surfaces as surfaces
from app.services.control_room import business_action_binding_producer as producer
from control_room_surface_fixtures import OPERATOR, VIEWER, action_item, snapshot


EVENT = "control_room_action_binding_operational_failure"


class MissingAuthorityTable(RuntimeError):
    pass


class GiSTAuthorityFailure(RuntimeError):
    pass


class CatalogFailure(RuntimeError):
    pass


class UnexpectedAuthorityFailure(RuntimeError):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    (
        MissingAuthorityTable("SELECT secret FROM missing_table /srv/private"),
        GiSTAuthorityFailure("23P01 binding=deadbeef" * 8),
        CatalogFailure("catalog workspace_id=private"),
        UnexpectedAuthorityFailure("handle=" + "a" * 64),
    ),
)
async def test_operational_failures_emit_one_sanitized_signal(
    caplog: pytest.LogCaptureFixture,
    failure: Exception,
):
    caplog.set_level(logging.ERROR)
    with (
        patch.object(producer.auth, "pool", new=AsyncMock(return_value=object())),
        patch.object(
            producer,
            "run_with_db_scope",
            new=AsyncMock(side_effect=failure),
        ),
    ):
        result = await producer.issue_action_bindings(
            OPERATOR,
            snapshot(items=(action_item(),)),
            enabled_template_ids={"create_followup_task"},
        )

    assert result == {}
    records = [record for record in caplog.records if record.message == EVENT]
    assert len(records) == 1
    rendered = records[0].getMessage()
    assert not any(
        value in rendered
        for value in ("SELECT", "/srv", "deadbeef", "workspace_id", "a" * 64)
    )
    assert records[0].exc_info is None
    assert records[0].event == EVENT
    assert records[0].component == "control_room_action_authority"
    assert records[0].outcome == "actions_omitted"


@pytest.mark.asyncio
async def test_expected_ineligibility_is_not_an_operational_failure(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.ERROR)
    assert (
        await producer.issue_action_bindings(
            VIEWER,
            snapshot(items=(action_item(),)),
            enabled_template_ids={"create_followup_task"},
        )
        == {}
    )
    assert (
        await producer.issue_action_bindings(
            OPERATOR,
            snapshot(items=(action_item(),)),
            enabled_template_ids=set(),
        )
        == {}
    )
    assert EVENT not in caplog.text


def _client() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = OPERATOR
        return await call_next(request)

    async def _current_user():
        return OPERATOR

    app.dependency_overrides[require_authenticated] = _current_user
    app.include_router(control_room.router)
    return TestClient(app, raise_server_exceptions=True)


def test_http_keeps_public_contract_and_emits_operational_signal(
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.ERROR)
    with (
        patch.object(
            surfaces,
            "collect_surface_snapshot",
            new=AsyncMock(return_value=snapshot(items=(action_item(),))),
        ),
        patch.object(
            surfaces,
            "load_enabled_action_template_ids",
            new=AsyncMock(return_value=frozenset({"create_followup_task"})),
        ),
        patch.object(producer.auth, "pool", new=AsyncMock(return_value=object())),
        patch.object(
            producer,
            "run_with_db_scope",
            new=AsyncMock(side_effect=MissingAuthorityTable("raw sql")),
        ),
    ):
        response = _client().get("/api/control-room/experience/v2")

    assert response.status_code == 200
    facts = [
        fact for section in response.json()["sections"] for fact in section["facts"]
    ]
    assert facts and all(fact["actions"] == [] for fact in facts)
    assert sum(record.message == EVENT for record in caplog.records) == 1
