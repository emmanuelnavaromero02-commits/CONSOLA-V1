from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service
from app.services.control_room.business_projection import filter_business_items


USER = {
    "id": 7,
    "role": "super_admin",
    "active_tenant_id": "tenant-A",
    "active_workspace_id": "workspace-A",
}


def _item(item_id: str, kind: str, **overrides) -> dict:
    return {
        "id": item_id,
        "kind": kind,
        "source_dataset": "gold_metrics",
        "evidence_refs": [f"evidence:{item_id}"],
        **overrides,
    }


@pytest.mark.asyncio
async def test_agentops_uses_canonical_business_ids_for_executive_counters():
    items = [
        _item("alert-good", "agent_alert"),
        _item("signal-good", "intelligence_signal"),
        _item("diagnostic", "source_state", data_status="missing"),
        _item(
            "alert-derived-diagnostic",
            "agent_alert",
            parent_item_id="diagnostic",
        ),
    ]
    snapshot = AsyncMock(return_value={"raw": True})
    forbidden_dashboard = AsyncMock(
        side_effect=AssertionError("agent polling must not fetch datasets")
    )

    async def scoped(_pool, _user, work):
        return await work(object(), "tenant-A", "workspace-A")

    with (
        patch.object(
            control_room_service,
            "persisted_business_projection",
            new=AsyncMock(return_value=filter_business_items(items)),
        ),
        patch.object(control_room_service, "dashboard", new=forbidden_dashboard),
        patch.object(
            control_room_service.auth, "pool", new=AsyncMock(return_value=object())
        ),
        patch.object(control_room_service, "_run_with_db_scope", new=scoped),
        patch.object(control_room_service, "_agentops_load_snapshot", new=snapshot),
        patch.object(
            control_room_service,
            "_agentops_payload_from_raw",
            side_effect=lambda raw, **_kwargs: raw,
        ),
    ):
        result = await control_room_service.agents_ops(USER)

    assert result == {"raw": True}
    forbidden_dashboard.assert_not_awaited()
    assert snapshot.await_args.kwargs["eligible_item_ids"] == [
        "alert-good",
        "signal-good",
    ]
    assert snapshot.await_args.kwargs["eligible_alert_ids"] == ["alert-good"]


class RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql: str, *args):
        self.calls.append((" ".join(sql.split()), args))
        return []


@pytest.mark.asyncio
async def test_agent_alert_orchestration_and_execution_queries_are_id_bounded():
    conn = RecordingConnection()

    await control_room_service._agentops_alert_rows(
        conn,
        workspace_id="workspace-A",
        allowed_param=None,
        eligible_alert_ids=["alert-good"],
    )
    await control_room_service._agentops_orchestration_rows(
        conn,
        workspace_id="workspace-A",
        table_exists={"decision_orchestration_runs": True},
        eligible_item_ids=["alert-good", "signal-good"],
    )
    await control_room_service._agentops_execution_rows(
        conn,
        workspace_id="workspace-A",
        table_exists={"decision_orchestration_executions": True},
        eligible_item_ids=["alert-good", "signal-good"],
    )

    assert "item_id = ANY($3::text[])" in conn.calls[0][0]
    assert conn.calls[0][1][2] == ["alert-good"]
    assert "source_id = ANY($2::text[])" in conn.calls[1][0]
    assert "'intelligence_signal'" in conn.calls[1][0]
    assert conn.calls[1][1][1] == ["alert-good", "signal-good"]
    assert "JOIN decision_orchestration_runs" in conn.calls[2][0]
    assert "run.source_id = ANY($2::text[])" in conn.calls[2][0]
    assert "'intelligence_signal'" in conn.calls[2][0]
