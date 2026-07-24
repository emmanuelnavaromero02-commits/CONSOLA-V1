from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.control_room.business_dismiss_reopen_with_audit import (
    dismiss_with_audit,
    reopen_with_audit,
)
from app.services.control_room.business_alert_operation_with_audit import (
    persist_alert_operation_with_audit,
)


USER = {
    "id": 7,
    "email": "owner@example.com",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}
ITEM = {
    "id": "business-1",
    "status": "dismissed",
    "decision_id": None,
    "selected_option_id": None,
    "execution_status": "not_started",
}


async def _run_operation(
    operation: str,
    *,
    run_scoped,
    persist_status_transition,
    record_audit_event,
    lock_item,
) -> None:
    common = {
        "pool": object(),
        "user": USER,
        "item": ITEM,
        "reason": "operator request",
        "ip": "127.0.0.1",
        "user_agent": "pytest",
        "run_scoped": run_scoped,
        "persist_status_transition": persist_status_transition,
        "ensure_item_row": AsyncMock(),
        "record_audit_event": record_audit_event,
    }
    if operation == "dismiss":
        await dismiss_with_audit(**common)
        return
    await reopen_with_audit(
        **common,
        lock_item=lock_item,
        reopen_allowed=lambda row: row["status"] == "dismissed",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("dismiss", "reopen"))
async def test_success_audit_runs_on_scope_connection_before_commit(
    operation: str,
) -> None:
    order: list[str] = []
    connection = object()
    scope_calls = 0

    async def run_scoped(_pool, _user, work):
        nonlocal scope_calls
        scope_calls += 1
        order.append("begin")
        await work(connection, "tenant-a", "workspace-a")
        order.append("commit")

    async def persist_status_transition(_conn, **_kwargs):
        order.append("transition")

    async def lock_item(_conn, **kwargs):
        order.append("lock")
        assert "status" not in kwargs["item"]
        assert "decision_id" not in kwargs["item"]
        return {"status": "dismissed"}

    async def record_audit_event(**kwargs):
        assert kwargs["connection"] is connection
        assert kwargs["critical"] is True
        assert "commit" not in order
        order.append(f"audit:{kwargs['action']}")

    await _run_operation(
        operation,
        run_scoped=run_scoped,
        persist_status_transition=persist_status_transition,
        record_audit_event=record_audit_event,
        lock_item=lock_item,
    )

    assert scope_calls == 1
    prefix = ["begin", "lock"] if operation == "reopen" else ["begin"]
    assert order == [
        *prefix,
        "transition",
        f"audit:control_room.{operation}",
        "commit",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("dismiss", "reopen"))
async def test_audit_failure_propagates_through_scope_rollback(operation: str) -> None:
    order: list[str] = []
    connection = object()

    async def run_scoped(_pool, _user, work):
        order.append("begin")
        try:
            await work(connection, "tenant-a", "workspace-a")
        except Exception:
            order.append("rollback")
            raise
        order.append("commit")

    async def persist_status_transition(_conn, **_kwargs):
        order.append("transition")

    async def lock_item(_conn, **_kwargs):
        order.append("lock")
        return {"status": "dismissed"}

    async def fail_audit(**kwargs):
        assert kwargs["connection"] is connection
        order.append(f"audit:{kwargs['action']}")
        raise RuntimeError(f"{operation} audit failed")

    with pytest.raises(RuntimeError, match=f"{operation} audit failed"):
        await _run_operation(
            operation,
            run_scoped=run_scoped,
            persist_status_transition=persist_status_transition,
            record_audit_event=fail_audit,
            lock_item=lock_item,
        )

    prefix = ["begin", "lock"] if operation == "reopen" else ["begin"]
    assert order == [
        *prefix,
        "transition",
        f"audit:control_room.{operation}",
        "rollback",
    ]


@pytest.mark.asyncio
async def test_false_positive_critical_audit_runs_before_scope_commit() -> None:
    order: list[str] = []
    connection = object()

    async def run_scoped(_pool, _user, work):
        order.append("begin")
        await work(connection, "tenant-a", "workspace-a")
        order.append("commit")

    async def persist_alert_state(_conn, **_kwargs):
        order.append("transition")

    async def record_audit_event(**kwargs):
        assert kwargs["connection"] is connection
        assert kwargs["critical"] is True
        order.append("audit")

    await persist_alert_operation_with_audit(
        object(),
        user=USER,
        item=ITEM,
        target_status="dismissed",
        terminal_statuses=("approved", "dismissed", "resolved"),
        alert_changes={"state": "false_positive"},
        alert_defaults={},
        alert_state={"state": "false_positive"},
        event_type="alert_false_positive",
        note="",
        reason="duplicate",
        audit_action="control_room.alert.false_positive",
        ip=None,
        user_agent=None,
        critical_audit=True,
        run_scoped=run_scoped,
        persist_alert_state=persist_alert_state,
        ensure_item_row=AsyncMock(),
        record_audit_event=record_audit_event,
    )

    assert order == ["begin", "transition", "audit", "commit"]
