from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_access import expected_item_owner
from app.services.control_room.business_item_persistence import parse_command_tag


ItemWriter = Callable[..., Awaitable[None]]


def command_count(result: Any, command: str) -> int:
    return parse_command_tag(result, command)


def _require_count(result: Any, command: str, expected: int = 1) -> None:
    if command_count(result, command) != expected:
        if command == "UPDATE":
            raise HTTPException(404, "control room item not found")
        raise RuntimeError(f"control room {command.lower()} affected unexpected rows")


async def _record_event(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    event_type: str,
    metadata: Mapping[str, Any],
) -> None:
    result = await conn.execute(
        """
        INSERT INTO control_room_item_events (
            tenant_id, workspace_id, item_id, event_type,
            actor_id, actor_email, metadata
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        """,
        user.get("active_tenant_id") or user.get("tenant_id"),
        user.get("active_workspace_id") or user.get("workspace_id"),
        item["id"],
        event_type,
        user.get("id"),
        user.get("email"),
        json.dumps(dict(metadata)),
    )
    _require_count(result, "INSERT")


async def _ensure(
    ensure_item_row: ItemWriter,
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    status: str,
) -> None:
    await ensure_item_row(
        conn,
        user=dict(user),
        item=dict(item),
        status=status,
        critical=True,
    )


def _owner(item: Mapping[str, Any], user: Mapping[str, Any]) -> int | None:
    return expected_item_owner(item, user)


async def persist_step(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    workspace_id: str,
    event_type: str,
    metadata: Mapping[str, Any],
    ensure_item_row: ItemWriter,
) -> None:
    await _ensure(
        ensure_item_row,
        conn,
        user=user,
        item=item,
        status=str(item.get("status") or "open"),
    )
    result = await conn.execute(
        """
        UPDATE control_room_items
           SET last_seen_at = NOW()
         WHERE workspace_id = $1
           AND item_id = $2
           AND owner_user_id IS NOT DISTINCT FROM $3
        """,
        workspace_id,
        item["id"],
        _owner(item, user),
    )
    _require_count(result, "UPDATE")
    await _record_event(
        conn, user=user, item=item, event_type=event_type, metadata=metadata
    )


async def persist_control(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    workspace_id: str,
    target_status: str,
    terminal_statuses: Sequence[str],
    control_state: Mapping[str, Any],
    event_type: str,
    event_metadata: Mapping[str, Any],
    ensure_item_row: ItemWriter,
) -> None:
    await _ensure(
        ensure_item_row,
        conn,
        user=user,
        item=item,
        status=target_status,
    )
    result = await conn.execute(
        """
        UPDATE control_room_items
           SET status = CASE WHEN status = ANY($5::text[]) THEN status ELSE $6 END,
               metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb,
               last_seen_at = NOW()
         WHERE workspace_id = $2
           AND item_id = $3
           AND owner_user_id IS NOT DISTINCT FROM $4
        """,
        json.dumps({"control_state": dict(control_state)}),
        workspace_id,
        item["id"],
        _owner(item, user),
        list(terminal_statuses),
        target_status,
    )
    _require_count(result, "UPDATE")
    await _record_event(
        conn,
        user=user,
        item=item,
        event_type=event_type,
        metadata=event_metadata,
    )


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
) -> None:
    await _ensure(
        ensure_item_row,
        conn,
        user=user,
        item=item,
        status=target_status,
    )
    if target_status == "dismissed":
        sql = """
            UPDATE control_room_items
               SET status = 'dismissed',
                   dismissed_at = COALESCE(dismissed_at, NOW()),
                   last_seen_at = NOW()
             WHERE workspace_id = $1 AND item_id = $2
               AND owner_user_id IS NOT DISTINCT FROM $3
        """
    elif target_status == "open":
        sql = """
            UPDATE control_room_items
               SET status = 'open', decision_id = NULL, resolved_at = NULL,
                   dismissed_at = NULL, last_seen_at = NOW()
             WHERE workspace_id = $1 AND item_id = $2
               AND owner_user_id IS NOT DISTINCT FROM $3
        """
    else:  # pragma: no cover - callers use the two declared transitions.
        raise ValueError("unsupported control room status transition")
    result = await conn.execute(sql, workspace_id, item["id"], _owner(item, user))
    _require_count(result, "UPDATE")
    await _record_event(
        conn,
        user=user,
        item=item,
        event_type=event_type,
        metadata={"reason": reason},
    )


async def persist_alert_state(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    workspace_id: str,
    target_status: str,
    terminal_statuses: Sequence[str],
    alert_state: Mapping[str, Any],
    event_type: str,
    note: str,
    reason: str,
    ensure_item_row: ItemWriter,
) -> None:
    await _ensure(
        ensure_item_row,
        conn,
        user=user,
        item=item,
        status=target_status,
    )
    result = await conn.execute(
        """
        UPDATE control_room_items
           SET status = CASE
                   WHEN $6 = 'dismissed' THEN 'dismissed'
                   WHEN status = ANY($5::text[]) THEN status ELSE $6
               END,
               metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb,
               dismissed_at = CASE WHEN $6 = 'dismissed'
                   THEN COALESCE(dismissed_at, NOW()) ELSE dismissed_at END,
               last_seen_at = NOW()
         WHERE workspace_id = $2 AND item_id = $3
           AND owner_user_id IS NOT DISTINCT FROM $4
        """,
        json.dumps({"alert_state": dict(alert_state)}),
        workspace_id,
        item["id"],
        _owner(item, user),
        list(terminal_statuses),
        target_status,
    )
    _require_count(result, "UPDATE")
    await _record_event(
        conn,
        user=user,
        item=item,
        event_type=event_type,
        metadata={"alert_state": dict(alert_state), "note": note, "reason": reason},
    )


__all__ = (
    "command_count",
    "persist_alert_state",
    "persist_control",
    "persist_status_transition",
    "persist_step",
)
