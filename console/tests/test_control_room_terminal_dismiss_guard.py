from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.control_room.business_action_mutations import (
    persist_status_transition,
)
from app.services.control_room.business_item_persistence import (
    PersistenceCommandTagError,
)
from app.services.control_room.business_terminal_workflow_guard import (
    TERMINAL_WORKFLOW_STATUSES,
)


USER = {
    "id": 7,
    "email": "owner@example.com",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}


def _item(
    *,
    owner_user_id: int = 7,
    status: str = "open",
) -> dict[str, Any]:
    return {
        "id": "item-1",
        "owner_user_id": owner_user_id,
        "status": status,
    }


class DismissConnection:
    def __init__(
        self,
        *,
        status: str = "open",
        visible_key: tuple[str, str, int | None] = ("workspace-a", "item-1", 7),
        missing_locks: int = 0,
        update_tag: Any = "UPDATE 1",
        event_tag: Any = "INSERT 0 1",
    ) -> None:
        self.status = status
        self.visible_key = visible_key
        self.missing_locks = missing_locks
        self.update_tag = update_tag
        self.event_tag = event_tag
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        normalized = " ".join(sql.split())
        self.calls.append(("fetchrow", normalized, args))
        assert "SELECT item_id, status" in normalized
        assert "FOR UPDATE" in normalized
        if self.missing_locks:
            self.missing_locks -= 1
            return None
        if args != self.visible_key:
            return None
        return {"item_id": args[1], "status": self.status}

    async def execute(self, sql: str, *args: Any) -> Any:
        normalized = " ".join(sql.split())
        self.calls.append(("execute", normalized, args))
        if normalized.startswith("UPDATE control_room_items"):
            return self.update_tag
        if normalized.startswith("INSERT INTO control_room_item_events"):
            return self.event_tag
        raise AssertionError(normalized)


async def _dismiss(
    conn: DismissConnection,
    *,
    item: dict[str, Any] | None = None,
    workspace_id: str = "workspace-a",
    ensure: AsyncMock | None = None,
) -> AsyncMock:
    writer = ensure or AsyncMock()
    await persist_status_transition(
        conn,
        user=USER,
        item=item or _item(),
        workspace_id=workspace_id,
        target_status="dismissed",
        event_type="dismissed",
        reason="not actionable",
        ensure_item_row=writer,
    )
    return writer


def _execute_calls(conn: DismissConnection) -> list[tuple[str, str, tuple[Any, ...]]]:
    return [call for call in conn.calls if call[0] == "execute"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", sorted(TERMINAL_WORKFLOW_STATUSES))
async def test_dismiss_rejects_locked_terminal_before_dml_or_event(
    status: str,
) -> None:
    conn = DismissConnection(status=status)
    ensure = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await _dismiss(conn, ensure=ensure)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "terminal_item"
    assert exc.value.detail["status"] == status
    assert not _execute_calls(conn)
    ensure.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("workspace_id", "owner_user_id"),
    (("workspace-b", 7), ("workspace-a", 9)),
)
async def test_dismiss_hides_terminal_state_outside_owner_scope(
    workspace_id: str,
    owner_user_id: int,
) -> None:
    conn = DismissConnection(status="approved")
    ensure = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await _dismiss(
            conn,
            item=_item(owner_user_id=owner_user_id),
            workspace_id=workspace_id,
            ensure=ensure,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "control room item not found"
    assert not _execute_calls(conn)
    ensure.assert_awaited_once()


@pytest.mark.asyncio
async def test_dismiss_locks_then_updates_nonterminal_with_cas_and_event() -> None:
    conn = DismissConnection()
    ensure = await _dismiss(conn)

    assert [call[0] for call in conn.calls] == ["fetchrow", "execute", "execute"]
    update = conn.calls[1]
    assert "owner_user_id IS NOT DISTINCT FROM $3" in update[1]
    assert "<> ALL($4::text[])" in update[1]
    assert update[2][3] == sorted(TERMINAL_WORKFLOW_STATUSES)
    assert conn.calls[2][1].startswith("INSERT INTO control_room_item_events")
    ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_item_is_ensured_live_then_locked_and_dismissed() -> None:
    conn = DismissConnection(missing_locks=1)

    async def mark_ensure(*_args: Any, **_kwargs: Any) -> None:
        conn.calls.append(("ensure", "", ()))

    ensure = AsyncMock(side_effect=mark_ensure)
    await _dismiss(
        conn,
        item=_item(status="needs_decision"),
        ensure=ensure,
    )

    assert [call[0] for call in conn.calls] == [
        "fetchrow",
        "ensure",
        "fetchrow",
        "execute",
        "execute",
    ]
    assert ensure.await_args.kwargs["status"] == "needs_decision"
    assert ensure.await_args.kwargs["critical"] is True
    assert conn.calls[3][1].startswith("UPDATE control_room_items")
    assert conn.calls[4][1].startswith("INSERT INTO control_room_item_events")


@pytest.mark.asyncio
async def test_missing_terminal_live_item_rejects_before_ensure() -> None:
    conn = DismissConnection(missing_locks=1)
    ensure = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await _dismiss(
            conn,
            item=_item(status="approved"),
            ensure=ensure,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "terminal_item"
    assert [call[0] for call in conn.calls] == ["fetchrow"]
    ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_dismiss_cas_miss_returns_conflict_without_event() -> None:
    conn = DismissConnection(update_tag="UPDATE 0")
    ensure = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await _dismiss(conn, ensure=ensure)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "workflow_stage_changed"
    assert len(_execute_calls(conn)) == 1
    ensure.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tag", "error"),
    (("UPDATE 2", RuntimeError), ("UPDATE", PersistenceCommandTagError)),
)
async def test_dismiss_requires_exact_update_command_tag(
    tag: str,
    error: type[Exception],
) -> None:
    conn = DismissConnection(update_tag=tag)
    ensure = AsyncMock()

    with pytest.raises(error):
        await _dismiss(conn, ensure=ensure)

    assert len(_execute_calls(conn)) == 1
    ensure.assert_not_awaited()
