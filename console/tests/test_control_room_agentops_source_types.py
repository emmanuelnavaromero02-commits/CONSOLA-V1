from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service


class _CaptureConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql: str, *args):
        self.calls.append((sql, args))
        return []


def test_agentops_builds_canonical_typed_source_ids():
    result = control_room_service.agentops_source_ids(
        [
            {"id": "item-1", "kind": "anomaly"},
            {"id": "alert-1", "kind": "agent_alert"},
            {"id": "signal-1", "kind": "intelligence_signal"},
        ]
    )
    assert result == {
        "control_room_item": ["item-1"],
        "agent_alert": ["alert-1"],
        "intelligence_signal": ["signal-1"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "loader",
    ["_agentops_orchestration_rows", "_agentops_execution_rows"],
)
async def test_agentops_queries_require_canonical_source_type_and_matching_id(loader):
    conn = _CaptureConnection()
    await getattr(control_room_service, loader)(
        conn,
        workspace_id="workspace-A",
        table_exists={
            "decision_orchestration_runs": True,
            "decision_orchestration_executions": True,
        },
        eligible_source_ids={
            "control_room_item": ["item-1"],
            "agent_alert": ["alert-1"],
            "intelligence_signal": ["signal-1"],
        },
    )
    sql, args = conn.calls[0]
    assert "source_type = 'control_room_item'" in sql
    assert "source_type = 'agent_alert'" in sql
    assert "source_type = 'intelligence_signal'" in sql
    assert "custom_source" not in sql
    assert args[1:] == (["item-1"], ["alert-1"], ["signal-1"])


@pytest.mark.asyncio
async def test_agentops_monte_carlo_receives_only_intelligence_signal_ids():
    monte_carlo = AsyncMock(return_value=[])
    with (
        patch.object(control_room_service, "_agentops_monte_carlo_rows", monte_carlo),
        patch.object(
            control_room_service,
            "_agentops_calibration_rows",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            control_room_service,
            "_agentops_orchestration_rows",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            control_room_service, "_agentops_execution_rows", AsyncMock(return_value=[])
        ),
    ):
        await control_room_service._agentops_intelligence_rows(
            object(),
            workspace_id="workspace-A",
            table_exists={},
            eligible_source_ids={
                "control_room_item": ["item-1"],
                "agent_alert": ["alert-1"],
                "intelligence_signal": ["signal-1"],
            },
        )

    assert monte_carlo.await_args.kwargs["eligible_item_ids"] == ["signal-1"]
