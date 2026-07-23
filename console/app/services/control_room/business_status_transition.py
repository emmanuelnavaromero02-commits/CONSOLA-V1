from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_access import expected_item_owner
from app.services.control_room.business_item_persistence import parse_command_tag
from app.services.control_room.business_terminal_workflow_guard import (
    TERMINAL_WORKFLOW_STATUSES,
    lock_nonterminal_workflow_item,
    require_nonterminal_workflow,
)


ItemWriter = Callable[..., Awaitable[None]]
EventWriter = Callable[..., Awaitable[None]]


def _require_update_count(result: Any) -> None:
    if parse_command_tag(result, "UPDATE") != 1:
        raise HTTPException(404, "control room item not found")


def _require_dismiss_count(result: Any) -> None:
    count = parse_command_tag(result, "UPDATE")
    if count == 0:
        raise HTTPException(
            409,
            {
                "code": "workflow_stage_changed",
                "message": "control room workflow changed; reload before mutating",
            },
        )
    if count != 1:
        raise RuntimeError("control room update affected unexpected rows")


async def _lock_dismiss_target(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    workspace_id: str,
    ensure_item_row: ItemWriter,
) -> int | None:
    owner_user_id = expected_item_owner(item, user)
    locked = await lock_nonterminal_workflow_item(
        conn,
        workspace_id=workspace_id,
        item_id=str(item["id"]),
        owner_user_id=owner_user_id,
        operation="dismiss",
        allow_missing=True,
    )
    if locked is not None:
        return owner_user_id

    live_status = str(item.get("status") or "open").strip().lower() or "open"
    require_nonterminal_workflow({"status": live_status}, operation="dismiss")
    await ensure_item_row(
        conn,
        user=dict(user),
        item=dict(item),
        status=live_status,
        critical=True,
    )
    await lock_nonterminal_workflow_item(
        conn,
        workspace_id=workspace_id,
        item_id=str(item["id"]),
        owner_user_id=owner_user_id,
        operation="dismiss",
    )
    return owner_user_id


async def _dismiss(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    workspace_id: str,
    ensure_item_row: ItemWriter,
) -> None:
    owner_user_id = await _lock_dismiss_target(
        conn,
        user=user,
        item=item,
        workspace_id=workspace_id,
        ensure_item_row=ensure_item_row,
    )
    result = await conn.execute(
        """
        UPDATE control_room_items
           SET status = 'dismissed',
               dismissed_at = COALESCE(dismissed_at, NOW()),
               last_seen_at = NOW()
         WHERE workspace_id = $1 AND item_id = $2
           AND owner_user_id IS NOT DISTINCT FROM $3
           AND LOWER(
               COALESCE(NULLIF(BTRIM(status), ''), 'open')
           ) <> ALL($4::text[])
        """,
        workspace_id,
        item["id"],
        owner_user_id,
        sorted(TERMINAL_WORKFLOW_STATUSES),
    )
    _require_dismiss_count(result)


async def _reopen(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    workspace_id: str,
) -> None:
    result = await conn.execute(
        """
        UPDATE control_room_items
           SET status = 'open', resolved_at = NULL, dismissed_at = NULL,
               last_seen_at = NOW()
         WHERE workspace_id = $1 AND item_id = $2
           AND owner_user_id IS NOT DISTINCT FROM $3
           AND status = 'dismissed'
           AND decision_id IS NULL
           AND NULLIF(BTRIM(selected_option_id), '') IS NULL
           AND COALESCE(
               NULLIF(BTRIM(execution_status), ''),
               'not_started'
           ) = 'not_started'
           AND NOT (
               COALESCE(metadata, '{}'::jsonb)
               ? 'decision_eligibility_provenance'
           )
        """,
        workspace_id,
        item["id"],
        expected_item_owner(item, user),
    )
    _require_update_count(result)


async def persist_status_transition(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    workspace_id: str,
    target_status: str,
    event_type: str,
    reason: str,
    ensure_item_row: ItemWriter,
    record_event: EventWriter,
) -> None:
    if target_status == "dismissed":
        await _dismiss(
            conn,
            user=user,
            item=item,
            workspace_id=workspace_id,
            ensure_item_row=ensure_item_row,
        )
    elif target_status == "open":
        await _reopen(
            conn,
            user=user,
            item=item,
            workspace_id=workspace_id,
        )
    else:  # pragma: no cover - callers use the two declared transitions.
        raise ValueError("unsupported control room status transition")
    await record_event(
        conn,
        user=user,
        item=item,
        event_type=event_type,
        metadata={"reason": reason},
    )


__all__ = ("persist_status_transition",)
