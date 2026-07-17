from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domains.decisions.business_visibility import count_business_decisions


class _DecisionConnection:
    def __init__(self):
        now = datetime.now(UTC)
        self.decisions = [
            {
                "id": decision_id,
                "created_at": now - timedelta(minutes=decision_id),
                "kpis": [],
            }
            for decision_id in (1, 2, 3, 4)
        ]
        self.items = [
            {
                "decision_id": 2,
                "item_id": "business-2",
                "item_kind": "anomaly",
                "source_dataset": "gold_metrics",
                "metadata": {"data_status": "ready"},
            },
            {
                "decision_id": 3,
                "item_id": "technical-3",
                "item_kind": "source_state",
                "source_dataset": "gold_metrics",
                "metadata": {"data_status": "missing"},
            },
        ]

    async def fetch(self, query: str, *_args):
        normalized = " ".join(query.split())
        if "FROM decisions" in normalized:
            return self.decisions
        if "FROM control_room_items" in normalized:
            return self.items
        if "FROM decision_actions" in normalized:
            return [{"decision_id": 4}]
        raise AssertionError(f"unexpected query: {normalized}")


@pytest.mark.asyncio
async def test_open_decision_count_includes_manual_and_eligible_linked_only():
    conn = _DecisionConnection()

    count = await count_business_decisions(
        conn,
        sql=(
            "SELECT * FROM decisions WHERE workspace_id = $1 "
            "AND status = 'open' ORDER BY created_at DESC, id DESC"
        ),
        params=["workspace-a"],
        workspace_id="workspace-a",
    )

    assert count == 2
