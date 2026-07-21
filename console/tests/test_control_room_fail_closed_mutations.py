from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service


USER = {
    "id": 7,
    "email": "ops@example.com",
    "active_tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "active_workspace_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
}


def _item() -> dict:
    return {
        "id": "business-1",
        "kind": "anomaly",
        "cartridge": "sap_hcm",
        "anomaly_type": "headcount_change",
        "status": "open",
        "source_dataset": "gold_metrics",
        "metric_type": "count",
        "observed_value": 1,
        "population_count": 10,
        "observation_date": "2026-07-20",
        "evidence_refs": ["gold_metrics:business-1"],
    }


class ZeroRowPool:
    def __init__(self, *, insert_tag: str = "INSERT 0", update_tag: str = "UPDATE 0"):
        self.insert_tag = insert_tag
        self.update_tag = update_tag
        self.calls: list[str] = []

    async def execute(self, sql: str, *_args):
        command = sql.lstrip().split(maxsplit=1)[0].upper()
        self.calls.append(command)
        if command == "INSERT":
            return self.insert_tag
        if command == "UPDATE":
            return self.update_tag
        raise AssertionError(f"unexpected command: {command}")


class MissingActionRunPool:
    def __init__(self):
        self.fetchval_calls = 0

    async def fetchval(self, _sql: str, *_args):
        self.fetchval_calls += 1
        return None


async def _run_scoped_with(connection, _pool, user, operation):
    return await operation(
        connection,
        user["active_tenant_id"],
        user["active_workspace_id"],
    )


@pytest.mark.asyncio
async def test_record_item_event_rejects_zero_row_insert_when_critical():
    pool = ZeroRowPool()

    with pytest.raises(RuntimeError):
        await control_room_service._record_item_event(
            pool,
            user=USER,
            item=_item(),
            event_type="decision_created",
            metadata={"decision_id": 42},
            critical=True,
        )

    assert pool.calls == ["INSERT"]


@pytest.mark.asyncio
async def test_set_execution_status_rejects_zero_row_update_when_critical():
    pool = ZeroRowPool()

    with pytest.raises(RuntimeError):
        await control_room_service._set_execution_status(
            pool,
            user=USER,
            item=_item(),
            execution_status="running",
            critical=True,
        )

    assert pool.calls == ["UPDATE"]


@pytest.mark.asyncio
async def test_persist_lessons_rejects_zero_row_insert():
    pool = ZeroRowPool()

    with pytest.raises(RuntimeError):
        await control_room_service._persist_lessons(
            pool,
            user=USER,
            item=_item(),
            decision_id=42,
            lessons=["Require manager review"],
        )

    assert pool.calls == ["INSERT"]


@pytest.mark.asyncio
async def test_create_item_lesson_stops_before_event_and_success_audit_on_update_zero():
    connection = ZeroRowPool()
    audit = AsyncMock()
    item_event = AsyncMock()

    async def run_scoped(pool, user, operation):
        return await _run_scoped_with(connection, pool, user, operation)

    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=_item()),
        ),
        patch.object(
            control_room_service.auth,
            "pool",
            new=AsyncMock(return_value=object()),
        ),
        patch.object(
            control_room_service,
            "_run_with_db_scope",
            new=AsyncMock(side_effect=run_scoped),
        ),
        patch.object(control_room_service, "_ensure_item_row", new=AsyncMock()),
        patch.object(control_room_service, "_persist_lessons", new=AsyncMock()),
        patch.object(
            control_room_service,
            "_record_item_event",
            new=item_event,
        ),
        patch.object(control_room_service.audit_service, "record_event", audit),
    ):
        with pytest.raises(RuntimeError, match="update affected unexpected rows"):
            await control_room_service.create_item_lesson(
                "business-1",
                {"rule": "Require manager review"},
                USER,
            )

    assert connection.calls == ["UPDATE"]
    item_event.assert_not_awaited()
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_action_run_event_rejects_zero_row_insert():
    pool = ZeroRowPool()

    with pytest.raises(RuntimeError):
        await control_room_service._record_action_run_event(
            pool,
            user=USER,
            item=_item(),
            action_run_id=17,
            event_type="action_run.started",
            status="running",
            metadata={"mode": "manual"},
            critical=True,
        )

    assert pool.calls == ["INSERT"]


@pytest.mark.asyncio
async def test_record_action_run_stops_before_event_when_insert_returns_no_id():
    pool = MissingActionRunPool()
    action_event = AsyncMock()

    with patch.object(
        control_room_service,
        "_record_action_run_event",
        new=action_event,
    ):
        with pytest.raises(RuntimeError, match="action run was not persisted"):
            await control_room_service._record_action_run(
                pool,
                user=USER,
                item=_item(),
                template={
                    "template_id": "create_followup_task",
                    "risk_level": "low",
                    "requires_approval": True,
                },
                mode="manual",
                status="running",
                input_payload={"title": "Review anomaly"},
                critical=True,
            )

    assert pool.fetchval_calls == 1
    action_event.assert_not_awaited()
