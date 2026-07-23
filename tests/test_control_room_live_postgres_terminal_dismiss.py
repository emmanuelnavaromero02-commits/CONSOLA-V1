from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg
import pytest
from fastapi import HTTPException

from app.services.control_room.business_action_mutations import (
    persist_status_transition,
)
from app.services.control_room.business_item_ensure_command import (
    ensure_authoritative_item_row,
)
from app.services.control_room.business_policy_metadata import (
    business_policy_metadata,
)
from app.services.control_room.business_terminal_workflow_guard import (
    TERMINAL_WORKFLOW_STATUSES,
)
from tests.test_control_room_live_postgres_p16 import (
    _approve,
    _linked_item,
    _tx,
    _user,
)
from tests.test_control_room_live_postgres_workflows import (
    AsyncNoop,
    _item,
    _scope,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


async def _dismiss(
    conn: Any,
    *,
    user: dict[str, Any],
    item: dict[str, Any],
    workspace_id: str,
    ensure_item_row: Callable[..., Awaitable[None]] | None = None,
) -> None:
    await persist_status_transition(
        conn,
        user=user,
        item=item,
        workspace_id=workspace_id,
        target_status="dismissed",
        event_type="dismissed",
        reason="live terminal race",
        ensure_item_row=ensure_item_row or AsyncNoop(),
    )


async def _ensure_live_item(
    conn: Any,
    *,
    user: dict[str, Any],
    item: dict[str, Any],
    status: str,
    **_kwargs: Any,
) -> None:
    await ensure_authoritative_item_row(
        conn,
        user=user,
        item=item,
        status=status,
        allow_diagnostic_transition=False,
        impact_builder=lambda _item: {
            "estimate": 1,
            "currency": "USD",
            "confidence": 0.9,
        },
        metadata_builder=lambda candidate, _impact: business_policy_metadata(
            {},
            candidate,
        ),
        terminal_statuses=TERMINAL_WORKFLOW_STATUSES,
    )


async def _state(
    dsn: str,
    *,
    workspace_id: str,
    item_id: str,
) -> tuple[str, list[str]]:
    conn = await asyncpg.connect(dsn)
    try:
        status = await conn.fetchval(
            """SELECT status
                 FROM control_room_items
                WHERE workspace_id = $1 AND item_id = $2""",
            workspace_id,
            item_id,
        )
        events = await conn.fetch(
            """SELECT event_type
                 FROM control_room_item_events
                WHERE workspace_id = $1
                  AND item_id = $2
                  AND event_type = ANY($3::text[])
                ORDER BY id""",
            workspace_id,
            item_id,
            ["approved", "dismissed"],
        )
        return str(status), [str(row["event_type"]) for row in events]
    finally:
        await conn.close()


async def _compete(
    *,
    label: str,
    barrier: asyncio.Barrier,
    dsn: str,
    tenant_id: str,
    workspace_id: str,
    work: Callable[[Any], Awaitable[Any]],
) -> str | HTTPException:
    await barrier.wait()
    try:
        await _tx(dsn, tenant_id, workspace_id, work)
    except HTTPException as exc:
        return exc
    return label


@pytest.mark.asyncio
async def test_live_approve_vs_dismiss_has_one_terminal_winner(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    tenant_id, workspace_id, item = await _linked_item(
        postgres_with_real_init_schema,
        "terminal-dismiss-race",
    )
    user = _user(tenant_id, workspace_id)
    barrier = asyncio.Barrier(2)

    approved, dismissed = await asyncio.gather(
        _compete(
            label="approved",
            barrier=barrier,
            dsn=omega_console_live_dsn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            work=lambda conn: _approve(
                conn,
                user=user,
                item=item,
                workspace_id=workspace_id,
            ),
        ),
        _compete(
            label="dismissed",
            barrier=barrier,
            dsn=omega_console_live_dsn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            work=lambda conn: _dismiss(
                conn,
                user=user,
                item=item,
                workspace_id=workspace_id,
            ),
        ),
    )

    winners = [result for result in (approved, dismissed) if isinstance(result, str)]
    losers = [
        result for result in (approved, dismissed) if isinstance(result, HTTPException)
    ]
    assert len(winners) == len(losers) == 1
    assert losers[0].status_code == 409
    assert losers[0].detail["code"] == "terminal_item"

    status, events = await _state(
        postgres_with_real_init_schema,
        workspace_id=workspace_id,
        item_id=item["id"],
    )
    assert status == winners[0]
    assert events == winners


@pytest.mark.asyncio
async def test_live_approved_item_cannot_be_dismissed_afterward(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    tenant_id, workspace_id, item = await _linked_item(
        postgres_with_real_init_schema,
        "terminal-approved-then-dismiss",
    )
    user = _user(tenant_id, workspace_id)
    await _tx(
        omega_console_live_dsn,
        tenant_id,
        workspace_id,
        lambda conn: _approve(
            conn,
            user=user,
            item=item,
            workspace_id=workspace_id,
        ),
    )

    with pytest.raises(HTTPException) as exc:
        await _tx(
            omega_console_live_dsn,
            tenant_id,
            workspace_id,
            lambda conn: _dismiss(
                conn,
                user=user,
                item=item,
                workspace_id=workspace_id,
            ),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "terminal_item"
    assert exc.value.detail["status"] == "approved"
    status, events = await _state(
        postgres_with_real_init_schema,
        workspace_id=workspace_id,
        item_id=item["id"],
    )
    assert status == "approved"
    assert events == ["approved"]


@pytest.mark.asyncio
async def test_live_missing_eligible_item_is_ensured_then_dismissed(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> None:
    setup = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        tenant_id, workspace_id = await _scope(setup)
        item = {
            **_item(
                "terminal-missing-ensure",
                tenant_id,
                workspace_id,
            ),
            "status": "open",
        }
        assert not await setup.fetchval(
            """SELECT EXISTS(
                   SELECT 1
                     FROM control_room_items
                    WHERE workspace_id = $1 AND item_id = $2
               )""",
            workspace_id,
            item["id"],
        )
    finally:
        await setup.close()

    user = _user(tenant_id, workspace_id)
    await _tx(
        omega_console_live_dsn,
        tenant_id,
        workspace_id,
        lambda conn: _dismiss(
            conn,
            user=user,
            item=item,
            workspace_id=workspace_id,
            ensure_item_row=_ensure_live_item,
        ),
    )

    status, events = await _state(
        postgres_with_real_init_schema,
        workspace_id=workspace_id,
        item_id=item["id"],
    )
    assert status == "dismissed"
    assert events == ["dismissed"]
