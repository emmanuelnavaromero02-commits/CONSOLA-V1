from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_access import expected_item_owner
from app.services.control_room.business_item_persistence import parse_command_tag
from app.services.control_room.business_status_transition import (
    lock_dismiss_target,
    persist_status_transition as _persist_status_transition,
    require_dismiss_count,
)


ItemWriter = Callable[..., Awaitable[None]]
command_count = parse_command_tag
_owner = expected_item_owner


def _require_count(result: Any, command: str, expected: int = 1) -> None:
    if command_count(result, command) != expected:
        if command == "UPDATE":
            raise HTTPException(404, "control room item not found")
        raise RuntimeError(f"control room {command.lower()} affected unexpected rows")


def require_exact_count(result: Any, command: str) -> None:
    if command_count(result, command) != 1:
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
    control_id: str,
    baseline_control: Mapping[str, Any],
    control_changes: Mapping[str, Any],
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
           SET status = CASE WHEN status = ANY($7::text[]) THEN status ELSE $8 END,
               metadata = jsonb_set(
                   COALESCE(metadata, '{}'::jsonb),
                   '{control_state}',
                   COALESCE(metadata -> 'control_state', '{}'::jsonb)
                   || jsonb_build_object(
                       $1::text,
                       $2::jsonb
                       || COALESCE(metadata #> ARRAY['control_state', $1]::text[], '{}'::jsonb)
                       || $3::jsonb
                   ),
                   true
               ),
               last_seen_at = NOW()
         WHERE workspace_id = $4
           AND item_id = $5
           AND owner_user_id IS NOT DISTINCT FROM $6
        """,
        control_id,
        json.dumps(dict(baseline_control)),
        json.dumps(dict(control_changes)),
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
    await _persist_status_transition(
        conn,
        user=user,
        item=item,
        workspace_id=workspace_id,
        target_status=target_status,
        event_type=event_type,
        reason=reason,
        ensure_item_row=ensure_item_row,
        record_event=_record_event,
    )


async def persist_alert_state(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    workspace_id: str,
    target_status: str,
    terminal_statuses: Sequence[str],
    alert_changes: Mapping[str, Any],
    alert_defaults: Mapping[str, Any] | None,
    event_type: str,
    note: str,
    reason: str,
    ensure_item_row: ItemWriter,
) -> None:
    if target_status == "dismissed":
        owner_user_id = await lock_dismiss_target(
            conn,
            user=user,
            item=item,
            workspace_id=workspace_id,
            ensure_item_row=ensure_item_row,
        )
    else:
        await _ensure(
            ensure_item_row,
            conn,
            user=user,
            item=item,
            status=target_status,
        )
        owner_user_id = _owner(item, user)
    result = await conn.execute(
        """
        UPDATE control_room_items
           SET status = CASE
                   WHEN status = ANY($6::text[]) THEN status ELSE $7
               END,
               metadata = jsonb_set(
                   COALESCE(metadata, '{}'::jsonb),
                   '{alert_state}',
                   $1::jsonb
                   || COALESCE(metadata -> 'alert_state', '{}'::jsonb)
                   || $2::jsonb,
                   true
               ),
               dismissed_at = CASE WHEN $7 = 'dismissed'
                   THEN COALESCE(dismissed_at, NOW()) ELSE dismissed_at END,
               last_seen_at = NOW()
         WHERE workspace_id = $3 AND item_id = $4
           AND owner_user_id IS NOT DISTINCT FROM $5
           AND (
               $7 <> 'dismissed'
               OR LOWER(
                   COALESCE(NULLIF(BTRIM(status), ''), 'open')
               ) <> ALL($6::text[])
           )
        """,
        json.dumps(dict(alert_defaults or {})),
        json.dumps(dict(alert_changes)),
        workspace_id,
        item["id"],
        owner_user_id,
        list(terminal_statuses),
        target_status,
    )
    if target_status == "dismissed":
        require_dismiss_count(result)
    else:
        _require_count(result, "UPDATE")
    await _record_event(
        conn,
        user=user,
        item=item,
        event_type=event_type,
        metadata={
            "alert_state": {**dict(alert_defaults or {}), **dict(alert_changes)},
            "note": note,
            "reason": reason,
        },
    )


__all__ = (
    "command_count",
    "persist_alert_state",
    "persist_control",
    "persist_status_transition",
    "persist_step",
    "require_exact_count",
)
