from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any


ScopedRunner = Callable[..., Awaitable[Any]]
AlertWriter = Callable[..., Awaitable[None]]
ItemWriter = Callable[..., Awaitable[None]]
AuditRecorder = Callable[..., Awaitable[None]]


async def persist_alert_operation_with_audit(
    pool: Any,
    *,
    user: Mapping[str, Any],
    item: Mapping[str, Any],
    target_status: str,
    terminal_statuses: Sequence[str],
    alert_changes: Mapping[str, Any],
    alert_defaults: Mapping[str, Any] | None,
    alert_state: Mapping[str, Any],
    event_type: str,
    note: str,
    reason: str,
    audit_action: str,
    ip: str | None,
    user_agent: str | None,
    critical_audit: bool,
    run_scoped: ScopedRunner,
    persist_alert_state: AlertWriter,
    ensure_item_row: ItemWriter,
    record_audit_event: AuditRecorder,
) -> None:
    async def _record_audit(connection: Any | None = None) -> None:
        kwargs = {
            "user_id": user.get("id"),
            "email": user.get("email"),
            "action": audit_action,
            "resource_type": "control_room_alert",
            "resource_id": str(item["id"]),
            "ip": ip,
            "user_agent": user_agent,
            "status": "success",
            "metadata": {"alert_state": dict(alert_state), "item": dict(item)},
            "critical": critical_audit,
        }
        if connection is not None:
            kwargs["connection"] = connection
        await record_audit_event(**kwargs)

    async def _write(conn: Any, _tenant_id: str | None, workspace_id: str) -> None:
        await persist_alert_state(
            conn,
            user=user,
            item=item,
            workspace_id=workspace_id,
            target_status=target_status,
            terminal_statuses=terminal_statuses,
            alert_changes=alert_changes,
            alert_defaults=alert_defaults,
            event_type=event_type,
            note=note,
            reason=reason,
            ensure_item_row=ensure_item_row,
        )
        if critical_audit:
            await _record_audit(conn)

    await run_scoped(pool, dict(user), _write)
    if not critical_audit:
        await _record_audit()


__all__ = ("persist_alert_operation_with_audit",)
