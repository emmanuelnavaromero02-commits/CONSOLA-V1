from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi import HTTPException


ScopedRunner = Callable[..., Awaitable[Any]]
StatusTransition = Callable[..., Awaitable[None]]
ItemWriter = Callable[..., Awaitable[None]]
AuditRecorder = Callable[..., Awaitable[None]]
ItemLocker = Callable[..., Awaitable[Mapping[str, Any] | None]]
ReopenPolicy = Callable[[Mapping[str, Any]], bool]
TransitionGuard = Callable[[Any], Awaitable[None]]


async def _transition_with_audit(
    pool: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    reason: str,
    target_status: str,
    event_type: str,
    audit_action: str,
    ip: str | None,
    user_agent: str | None,
    run_scoped: ScopedRunner,
    persist_status_transition: StatusTransition,
    ensure_item_row: ItemWriter,
    record_audit_event: AuditRecorder,
    guard: TransitionGuard | None = None,
) -> None:
    async def _write(conn: Any, _tenant_id: str | None, workspace_id: str) -> None:
        if guard is not None:
            await guard(conn)
        await persist_status_transition(
            conn,
            user=user,
            item=item,
            workspace_id=workspace_id,
            target_status=target_status,
            event_type=event_type,
            reason=reason,
            ensure_item_row=ensure_item_row,
        )
        await record_audit_event(
            connection=conn,
            user_id=user.get("id"),
            email=user.get("email"),
            action=audit_action,
            resource_type="control_room_item",
            resource_id=str(item["id"]),
            ip=ip,
            user_agent=user_agent,
            status="success",
            metadata={"reason": reason, "item": dict(item)},
            critical=True,
        )

    await run_scoped(pool, dict(user), _write)


async def dismiss_with_audit(
    pool: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    reason: str,
    ip: str | None,
    user_agent: str | None,
    run_scoped: ScopedRunner,
    persist_status_transition: StatusTransition,
    ensure_item_row: ItemWriter,
    record_audit_event: AuditRecorder,
) -> None:
    await _transition_with_audit(
        pool,
        user=user,
        item=item,
        reason=reason,
        target_status="dismissed",
        event_type="dismissed",
        audit_action="control_room.dismiss",
        ip=ip,
        user_agent=user_agent,
        run_scoped=run_scoped,
        persist_status_transition=persist_status_transition,
        ensure_item_row=ensure_item_row,
        record_audit_event=record_audit_event,
    )


async def reopen_with_audit(
    pool: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    reason: str,
    ip: str | None,
    user_agent: str | None,
    run_scoped: ScopedRunner,
    persist_status_transition: StatusTransition,
    ensure_item_row: ItemWriter,
    record_audit_event: AuditRecorder,
    lock_item: ItemLocker,
    reopen_allowed: ReopenPolicy,
) -> None:
    async def _require_reopen_allowed(conn: Any) -> None:
        guard_item = dict(item)
        for field in (
            "decision_id",
            "selected_option_id",
            "status",
            "execution_status",
        ):
            guard_item.pop(field, None)
        locked = await lock_item(conn, user=user, item=guard_item)
        if locked is None or not reopen_allowed(locked):
            raise HTTPException(
                409,
                {
                    "code": "workflow_reopen_not_allowed",
                    "message": "only dismissed items without workflow state can be reopened",
                },
            )

    await _transition_with_audit(
        pool,
        user=user,
        item=item,
        reason=reason,
        target_status="open",
        event_type="reopened",
        audit_action="control_room.reopen",
        ip=ip,
        user_agent=user_agent,
        run_scoped=run_scoped,
        persist_status_transition=persist_status_transition,
        ensure_item_row=ensure_item_row,
        record_audit_event=record_audit_event,
        guard=_require_reopen_allowed,
    )


__all__ = ("dismiss_with_audit", "reopen_with_audit")
