from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.services.control_room.business_access import expected_item_owner


ScopedRunner = Callable[..., Awaitable[Any]]
ItemWriter = Callable[..., Awaitable[None]]
DecisionCreator = Callable[..., Awaitable[Any]]
DecisionApprover = Callable[..., Awaitable[Any]]
DecisionLinker = Callable[..., Awaitable[None]]
PostLinkHook = Callable[[Any, int], Awaitable[None]]


@dataclass(frozen=True)
class AtomicApproval:
    decision_id: int
    decision: Any | None
    action: Any
    item: dict[str, Any]
    implicit_decision: bool


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


async def _reconstruct_linked_item(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    workspace_id: str,
    decision_id: int,
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT tenant_id::text AS tenant_id,
               workspace_id::text AS workspace_id,
               owner_user_id, item_id, status, decision_id,
               selected_option_id, execution_status, metadata
          FROM control_room_items
         WHERE workspace_id = $1
           AND item_id = $2
           AND owner_user_id IS NOT DISTINCT FROM $3
         FOR UPDATE
        """,
        workspace_id,
        str(item["id"]),
        expected_item_owner(item, user),
    )
    if not row:
        raise HTTPException(404, "control room item not found")
    if str(row.get("decision_id") or "") != str(decision_id):
        raise HTTPException(409, "decision is not linked to control room item")
    return {
        **dict(item),
        "tenant_id": str(row.get("tenant_id") or item.get("tenant_id") or ""),
        "workspace_id": str(
            row.get("workspace_id") or item.get("workspace_id") or workspace_id
        ),
        "owner_user_id": row.get("owner_user_id"),
        "status": str(row.get("status") or "decision_created"),
        "decision_id": int(row["decision_id"]),
        "selected_option_id": row.get("selected_option_id"),
        "execution_status": str(row.get("execution_status") or "not_started"),
        "metadata": _metadata(row.get("metadata")),
    }


async def approve_with_optional_decision(
    pool: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    decision_id: int | None,
    lessons: Sequence[str],
    confidence: float,
    run_scoped: ScopedRunner,
    ensure_item_row: ItemWriter,
    record_item_event: ItemWriter,
    create_and_link: DecisionCreator,
    approve_item: DecisionApprover,
    link_decision: DecisionLinker,
    approve_link: DecisionLinker,
    post_link_hook: PostLinkHook | None = None,
) -> AtomicApproval:
    requested_decision_id = decision_id

    async def _write(
        conn: Any, _tenant_id: str | None, workspace_id: str
    ) -> AtomicApproval:
        current_item = dict(item)
        decision = None
        resolved_decision_id = requested_decision_id
        if resolved_decision_id is None:
            decision = await create_and_link(
                conn,
                user=user,
                item=current_item,
                workspace_id=workspace_id,
                ensure_item_row=ensure_item_row,
                record_item_event=record_item_event,
            )
            resolved_decision_id = int(decision["id"])
            if post_link_hook is not None:
                await post_link_hook(conn, resolved_decision_id)
            current_item = await _reconstruct_linked_item(
                conn,
                user=user,
                item=current_item,
                workspace_id=workspace_id,
                decision_id=resolved_decision_id,
            )

        action = await approve_item(
            conn,
            user=user,
            item=current_item,
            workspace_id=workspace_id,
            decision_id=int(resolved_decision_id),
            lessons=lessons,
            confidence=confidence,
            ensure_item_row=ensure_item_row,
            link_decision=link_decision,
            approve_link=approve_link,
        )
        return AtomicApproval(
            decision_id=int(resolved_decision_id),
            decision=decision,
            action=action,
            item=current_item,
            implicit_decision=requested_decision_id is None,
        )

    return await run_scoped(pool, dict(user), _write)


__all__ = ("AtomicApproval", "approve_with_optional_decision")
