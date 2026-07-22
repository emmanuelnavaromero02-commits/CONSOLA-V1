from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_access import expected_item_owner
from app.services.control_room.business_item_persistence import parse_command_tag


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
    if target_status == "dismissed":
        await _ensure(
            ensure_item_row,
            conn,
            user=user,
            item=item,
            status=target_status,
        )
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
               SET status = 'open', resolved_at = NULL, dismissed_at = NULL,
                   last_seen_at = NOW()
             WHERE workspace_id = $1 AND item_id = $2
               AND owner_user_id IS NOT DISTINCT FROM $3
               AND status = 'dismissed'
               AND decision_id IS NULL
               AND NULLIF(BTRIM(selected_option_id), '') IS NULL
               AND COALESCE(NULLIF(BTRIM(execution_status), ''), 'not_started') = 'not_started'
               AND NOT (
                   COALESCE(metadata, '{}'::jsonb)
                   ? 'decision_eligibility_provenance'
               )
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
    alert_changes: Mapping[str, Any],
    alert_defaults: Mapping[str, Any] | None,
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
                   WHEN $7 = 'dismissed' THEN 'dismissed'
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
        """,
        json.dumps(dict(alert_defaults or {})),
        json.dumps(dict(alert_changes)),
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
