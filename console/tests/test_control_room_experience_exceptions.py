from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.routers import control_room_surfaces as surfaces
from app.services.control_room import business_exception_resolution as resolution
from app.services.control_room.business_action_public_projection import public_action
from app.services.control_room.business_exception_resolution import (
    ExceptionResolution,
    exception_resolution,
    load_exception_resolutions,
)
from app.services.control_room.business_experience_v2 import (
    build_business_experience_v2,
)
from control_room_surface_fixtures import (
    OPERATOR,
    TENANT_ID,
    VIEWER,
    WORKSPACE_ID,
    business_item,
    snapshot,
)


APPROVED_AT = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)


def _resolution(**updates: Any) -> ExceptionResolution:
    values = {
        "approved_at": APPROVED_AT,
        "reason": "Proveedor validado por auditoría interna",
        "actor_user_id": 9,
    }
    values.update(updates)
    return ExceptionResolution(**values)


def _build(items, resolutions, *, user=OPERATOR, actions=None):
    return build_business_experience_v2(
        snapshot(items=tuple(items)),
        user=user,
        enabled_template_ids=frozenset(),
        actions_by_item=actions if actions is not None else {},
        exception_resolutions=resolutions,
    )


def test_approved_exceptions_leave_sections_and_list_with_reopen_only():
    dismissed = business_item("business-1", status="dismissed")
    open_item = business_item("business-2", title="Otro hallazgo")
    actions = {
        "business-1": (
            public_action("a" * 64, template_id="reopen_exception"),
            public_action("b" * 64, template_id="approve_exception"),
        ),
        "business-2": (
            public_action("c" * 64, template_id="approve_exception"),
            public_action("d" * 64, template_id="reopen_exception"),
        ),
    }
    response = _build(
        (dismissed, open_item),
        {"business-1": _resolution()},
        actions=actions,
    )

    titles = [fact.title for section in response.sections for fact in section.facts]
    assert titles == ["Otro hallazgo"]
    fact = response.sections[0].facts[0]
    assert [action.kind for action in fact.actions] == ["exception_approval"]
    (exception,) = response.exceptions
    assert exception.title == "Observed business condition"
    assert exception.entity_label == "Observed employee"
    assert exception.approved_at == APPROVED_AT
    assert exception.reason == "Proveedor validado por auditoría interna"
    assert exception.approved_by_you is True
    assert [action.kind for action in exception.actions] == ["exception_reopen"]
    payload = response.model_dump(mode="json")
    assert "item_id" not in str(payload)
    assert "business-1" not in str(payload)


def test_exception_fields_come_only_from_present_values():
    item = business_item("business-1", status="dismissed")
    response = _build(
        (item,),
        {"business-1": _resolution(approved_at=None, reason=None, actor_user_id=4)},
    )
    (exception,) = response.exceptions
    assert exception.approved_at is None
    assert exception.reason is None
    assert exception.approved_by_you is False
    assert exception.actions == []


def test_dismissed_without_resolution_and_resolved_items_are_not_exceptions():
    response = _build(
        (
            business_item("business-1", status="dismissed"),
            business_item("business-2", status="resolved"),
        ),
        {"business-2": _resolution()},
    )
    assert response.sections == []
    assert response.exceptions == []


def test_viewer_sees_exceptions_without_actions():
    response = build_business_experience_v2(
        snapshot(items=(business_item("business-1", status="dismissed"),)),
        user=VIEWER,
        enabled_template_ids=frozenset(),
        actions_by_item=None,
        exception_resolutions={"business-1": _resolution()},
    )
    (exception,) = response.exceptions
    assert exception.actions == []
    assert exception.approved_by_you is False


def test_exceptions_are_bounded_and_newest_first():
    items = [
        business_item(f"business-{index}", status="dismissed") for index in range(25)
    ]
    resolutions = {
        f"business-{index}": _resolution(
            approved_at=datetime(2026, 9, 1 + index, tzinfo=UTC)
        )
        for index in range(25)
    }
    response = _build(items, resolutions)

    assert len(response.exceptions) == 20
    moments = [exception.approved_at for exception in response.exceptions]
    assert moments == sorted(moments, reverse=True)
    assert moments[0] == datetime(2026, 9, 25, tzinfo=UTC)


def test_resolution_metadata_parsing_is_strict():
    assert exception_resolution({"resolution": "false_positive"}) is None
    parsed = exception_resolution(
        {
            "resolution": "exception_approved",
            "resolution_at": "2026-09-25T10:00:00Z",
            "resolution_reason": "  Motivo válido  ",
            "resolution_actor_id": "9",
        }
    )
    assert parsed == ExceptionResolution(APPROVED_AT, "Motivo válido", 9)
    bad = exception_resolution(
        {
            "resolution": "exception_approved",
            "resolution_at": "ayer",
            "resolution_reason": 12,
            "resolution_actor_id": True,
        }
    )
    assert bad == ExceptionResolution(None, None, None)


class ReadConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append((" ".join(query.split()), args))
        return "SELECT 1"

    async def fetch(self, query: str, *args: Any):
        statement = " ".join(query.split())
        self.calls.append((statement, args))
        return self.rows


@pytest.mark.asyncio
async def test_resolution_read_is_select_only_scoped_and_skipped_without_dismissed():
    conn = ReadConn(
        [
            {
                "item_id": "business-1",
                "metadata": '{"resolution": "exception_approved", "resolution_actor_id": 9}',
            },
            {"item_id": "business-3", "metadata": {"resolution": "other"}},
        ]
    )
    pool = AsyncMock(return_value=conn)
    with patch.object(resolution.auth, "pool", new=pool):
        assert (
            await load_exception_resolutions(
                OPERATOR, snapshot(items=(business_item(),))
            )
            == {}
        )
        pool.assert_not_awaited()
        loaded = await load_exception_resolutions(
            {**OPERATOR, "role": "analyst"},
            snapshot(items=(business_item("business-1", status="dismissed"),)),
        )

    assert set(loaded) == {"business-1"}
    statements = [statement for statement, _ in conn.calls]
    assert statements[0].upper().startswith("SELECT SET_CONFIG")
    select_args = conn.calls[1][1]
    assert select_args[:4] == (WORKSPACE_ID, TENANT_ID, ["business-1"], 9)
    for statement in statements:
        assert not any(
            keyword in statement.upper()
            for keyword in ("INSERT ", "UPDATE ", "DELETE ", "FOR UPDATE", "FOR SHARE")
        )


@pytest.mark.asyncio
async def test_resolution_read_failure_omits_exceptions_with_a_sanitized_signal(caplog):
    caplog.set_level(logging.ERROR)
    with patch.object(
        resolution.auth,
        "pool",
        new=AsyncMock(side_effect=RuntimeError("SELECT secret FROM /srv")),
    ):
        loaded = await load_exception_resolutions(
            OPERATOR, snapshot(items=(business_item("business-1", status="dismissed"),))
        )
    assert loaded == {}
    assert "control_room_exception_resolution_read_failure" in caplog.text
    assert "/srv" not in caplog.text


@pytest.mark.asyncio
async def test_v2_route_reads_resolutions_and_publishes_exceptions():
    item = business_item("business-1", status="dismissed")
    current = snapshot(items=(item,))
    loader = AsyncMock(return_value={"business-1": _resolution()})
    with (
        patch.object(
            surfaces, "collect_surface_snapshot", new=AsyncMock(return_value=current)
        ),
        patch.object(
            surfaces,
            "load_enabled_action_template_ids",
            new=AsyncMock(return_value=frozenset({"reopen_exception"})),
        ),
        patch.object(
            surfaces,
            "issue_action_bindings",
            new=AsyncMock(
                return_value={
                    "business-1": (
                        public_action("a" * 64, template_id="reopen_exception"),
                    )
                }
            ),
        ),
        patch.object(surfaces, "load_exception_resolutions", new=loader),
    ):
        response = await surfaces.control_room_experience_v2(OPERATOR)

    loader.assert_awaited_once_with(OPERATOR, current)
    assert response.sections == []
    assert [exception.actions[0].label for exception in response.exceptions] == [
        "Reabrir hallazgo"
    ]
