from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.control_room.business_item_approval_guard import (
    IMPLICIT_SELF_APPROVAL,
    ItemApprovalDenied,
    record_item_approval_denial,
)


ScopedRunner = Callable[..., Awaitable[Any]]
ItemWriter = Callable[..., Awaitable[None]]
DecisionApprover = Callable[..., Awaitable[Any]]
DecisionLinker = Callable[..., Awaitable[None]]
AuditRecorder = Callable[..., Awaitable[None]]


@dataclass(frozen=True)
class AtomicApproval:
    decision_id: int
    action: Any
    item: dict[str, Any]


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
    approve_item: DecisionApprover,
    link_decision: DecisionLinker,
    approve_link: DecisionLinker,
    record_audit_event: AuditRecorder,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AtomicApproval:
    async def _deny(denial: ItemApprovalDenied) -> None:
        await record_item_approval_denial(
            record_audit_event,
            user=user,
            item_id=str(item["id"]),
            denial=denial,
            requested_decision_id=decision_id,
            ip=ip,
            user_agent=user_agent,
        )

    if decision_id is None:
        denial = ItemApprovalDenied(IMPLICIT_SELF_APPROVAL)
        await _deny(denial)
        raise denial
    resolved_decision_id = int(decision_id)

    async def _write(
        conn: Any, _tenant_id: str | None, workspace_id: str
    ) -> AtomicApproval:
        current_item = dict(item)
        action = await approve_item(
            conn,
            user=user,
            item=current_item,
            workspace_id=workspace_id,
            decision_id=resolved_decision_id,
            lessons=lessons,
            confidence=confidence,
            ensure_item_row=ensure_item_row,
            link_decision=link_decision,
            approve_link=approve_link,
        )
        await record_audit_event(
            connection=conn,
            user_id=user.get("id"),
            email=user.get("email"),
            action="control_room.approve",
            resource_type="control_room_item",
            resource_id=str(item["id"]),
            ip=ip,
            user_agent=user_agent,
            status="success",
            metadata={"decision_id": resolved_decision_id, "item": current_item},
            critical=True,
        )
        return AtomicApproval(
            decision_id=resolved_decision_id,
            action=action,
            item=current_item,
        )

    try:
        return await run_scoped(pool, dict(user), _write)
    except ItemApprovalDenied as denial:
        await _deny(denial)
        raise


__all__ = ("AtomicApproval", "approve_with_optional_decision")
