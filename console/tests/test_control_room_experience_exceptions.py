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
    APPROVED_EXCEPTIONS_SQL,
    ApprovedException,
    ExceptionResolution,
    exception_resolution,
    load_approved_exceptions,
)
from app.services.control_room.business_experience_v2 import (
    build_business_experience_v2,
)
from control_room_direct_action_fixtures import exception_item, exception_row
from control_room_surface_fixtures import (
    OPERATOR,
    TENANT_ID,
    VIEWER,
    WORKSPACE_ID,
    business_item,
    snapshot,
)


APPROVED_AT = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
REASON = "Proveedor validado por auditoría interna"


def _record(
    item_id: str = "business-1", **resolution_updates: Any
) -> ApprovedException:
    values = {"approved_at": APPROVED_AT, "reason": REASON, "actor_user_id": 9}
    values.update(resolution_updates)
    return ApprovedException(
        item_id,
        exception_row(exception_item(item_id)),
        ExceptionResolution(**values),
    )


def _build(items, records, *, user=OPERATOR, reopen=None):
    return build_business_experience_v2(
        snapshot(items=tuple(items)),
        user=user,
        enabled_template_ids=frozenset(),
        actions_by_item={},
        approved_exceptions=records,
        reopen_actions=reopen or {},
    )


def test_exceptions_come_from_approval_records_even_without_a_live_fact():
    open_item = business_item("business-2", title="Otro hallazgo")
    response = _build(
        (open_item,),
        [_record("business-1")],
        reopen={"business-1": public_action("a" * 64, template_id="reopen_exception")},
    )

    assert [fact.title for fact in response.sections[0].facts] == ["Otro hallazgo"]
    (exception,) = response.exceptions
    assert exception.title == "Observed business condition"
    assert exception.entity_label == "Observed employee"
    assert exception.approved_at == APPROVED_AT
    assert exception.reason == REASON
    assert exception.approved_by_you is True
    assert exception.observed_at is not None
    assert [action.kind for action in exception.actions] == ["exception_reopen"]
    payload = str(response.model_dump(mode="json"))
    assert "business-1" not in payload and "item_id" not in payload


def test_a_drifted_exception_shows_as_an_open_fact_instead_of_an_exception():
    live_again = business_item("business-1", title="Observed business condition")
    response = _build((live_again,), [_record("business-1")])

    assert [fact.title for fact in response.sections[0].facts] == [
        "Observed business condition"
    ]
    assert response.exceptions == []


def test_a_dismissed_live_item_keeps_its_exception_listed():
    dismissed = business_item("business-1", status="dismissed")
    response = _build((dismissed,), [_record("business-1")])

    assert response.sections == []
    assert len(response.exceptions) == 1


def test_exception_fields_come_only_from_present_values():
    response = _build(
        (),
        [_record("business-1", approved_at=None, reason=None, actor_user_id=4)],
        reopen={"business-1": public_action("a" * 64, template_id="approve_exception")},
    )
    (exception,) = response.exceptions
    assert exception.approved_at is None
    assert exception.reason is None
    assert exception.approved_by_you is False
    assert exception.actions == []


def test_exceptions_are_bounded_and_newest_first():
    records = [
        _record(
            f"business-{index}",
            approved_at=datetime(2026, 9, 1 + index, tzinfo=UTC),
        )
        for index in range(25)
    ]
    response = _build((), records)

    assert len(response.exceptions) == 20
    moments = [exception.approved_at for exception in response.exceptions]
    assert moments == sorted(moments, reverse=True)


def test_viewer_sees_exceptions_without_actions():
    response = _build((), [_record("business-1")], user=VIEWER)
    (exception,) = response.exceptions
    assert exception.actions == []
    assert exception.approved_by_you is False


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
    assert exception_resolution(
        {
            "resolution": "exception_approved",
            "resolution_at": "ayer",
            "resolution_reason": 12,
            "resolution_actor_id": True,
        }
    ) == ExceptionResolution(None, None, None)


class ReadConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append((" ".join(query.split()), args))
        return "SELECT 1"

    async def fetch(self, query: str, *args: Any):
        self.calls.append((" ".join(query.split()), args))
        return self.rows


@pytest.mark.asyncio
async def test_loader_reads_approval_records_newest_first_with_scope_filters():
    rows = [
        exception_row(exception_item("business-1")),
        exception_row(exception_item("business-2"), status="open"),
        exception_row(
            exception_item("business-3"),
            metadata_updates={"resolution": "false_positive"},
        ),
    ]
    conn = ReadConn(rows)
    with patch.object(resolution.auth, "pool", new=AsyncMock(return_value=conn)):
        loaded = await load_approved_exceptions({**OPERATOR, "role": "analyst"})

    assert [record.item_id for record in loaded] == ["business-1"]
    statements = [statement for statement, _ in conn.calls]
    assert statements[0].upper().startswith("SELECT SET_CONFIG")
    select, args = conn.calls[1]
    assert select == " ".join(APPROVED_EXCEPTIONS_SQL.split())
    assert "ORDER BY item.metadata->>'resolution_at' DESC" in select
    assert "status = 'dismissed'" in select
    assert args == (WORKSPACE_ID, TENANT_ID, 9, ["platform", "sap_hcm"], 20)
    for statement in statements:
        assert not any(
            keyword in statement.upper()
            for keyword in ("INSERT ", "UPDATE ", "DELETE ", "FOR UPDATE", "FOR SHARE")
        )


@pytest.mark.asyncio
async def test_loader_failure_omits_exceptions_with_a_sanitized_signal(caplog):
    caplog.set_level(logging.ERROR)
    with patch.object(
        resolution.auth,
        "pool",
        new=AsyncMock(side_effect=RuntimeError("SELECT secret FROM /srv")),
    ):
        assert await load_approved_exceptions(OPERATOR) == []
    assert "control_room_exception_resolution_read_failure" in caplog.text
    assert "/srv" not in caplog.text


@pytest.mark.asyncio
async def test_v2_route_lists_records_and_issues_reopen_for_them():
    records = [_record("business-1")]
    loader = AsyncMock(return_value=records)
    reopen = AsyncMock(
        return_value={
            "business-1": public_action("a" * 64, template_id="reopen_exception")
        }
    )
    with (
        patch.object(
            surfaces,
            "collect_surface_snapshot",
            new=AsyncMock(return_value=snapshot(items=())),
        ),
        patch.object(
            surfaces,
            "load_enabled_action_template_ids",
            new=AsyncMock(return_value=frozenset({"reopen_exception"})),
        ),
        patch.object(surfaces, "issue_action_bindings", new=AsyncMock(return_value={})),
        patch.object(surfaces, "load_approved_exceptions", new=loader),
        patch.object(surfaces, "issue_reopen_bindings", new=reopen),
    ):
        response = await surfaces.control_room_experience_v2(OPERATOR)

    loader.assert_awaited_once_with(OPERATOR)
    assert list(reopen.await_args.args[1]) == ["business-1"]
    assert reopen.await_args.kwargs["enabled_template_ids"] == frozenset(
        {"reopen_exception"}
    )
    assert [e.actions[0].label for e in response.exceptions] == ["Reabrir hallazgo"]
