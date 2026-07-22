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
        "source_dataset": "gold_metrics",
    }


class ZeroRowPool:
    def __init__(self):
        self.calls: list[str] = []

    async def execute(self, sql: str, *_args):
        self.calls.append(sql.lstrip().split(maxsplit=1)[0].upper())
        return "INSERT 0"


class MissingActionRunPool:
    def __init__(self):
        self.fetchval_calls = 0

    async def fetchval(self, _sql: str, *_args):
        self.fetchval_calls += 1
        return None


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
