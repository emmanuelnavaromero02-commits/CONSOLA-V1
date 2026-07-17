from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domains.decisions.business_visibility import fetch_business_decisions
from app.services.control_room.business_item_reader import (
    fetch_eligible_persisted_items,
)
from app.services.control_room.business_projection import (
    normalize_persisted_business_item,
)


WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"


def _persisted_item(index: int, *, eligible: bool = False) -> dict:
    return {
        "tenant_id": None,
        "workspace_id": WORKSPACE_ID,
        "owner_user_id": None,
        "item_id": f"item-{index:04d}",
        "cartridge_id": "platform",
        "domain": "Operacion",
        "source_dataset": "gold_metrics",
        "item_kind": "intelligence_signal" if eligible else "source_state",
        "title": f"Item {index}",
        "severity": "high",
        "status": "open",
        "decision_id": None,
        "entity_kind": "Metric",
        "entity_id": str(index),
        "entity_label": str(index),
        "anomaly_type": "metric",
        "metadata": (
            {"evidence_refs": [f"evidence:{index}"]}
            if eligible
            else {"data_status": "missing"}
        ),
        "first_seen_at": datetime(2026, 7, 1, tzinfo=UTC),
        "last_seen_at": datetime(2026, 7, 16, tzinfo=UTC) - timedelta(seconds=index),
        "resolved_at": None,
        "dismissed_at": None,
        "impact_estimate": None,
        "impact_currency": None,
        "confidence": None,
        "priority_score": 1000 - index,
        "selected_option_id": None,
        "execution_status": "not_started",
    }


class ItemPages:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, sql: str, *_args):
        assert "FROM control_room_items" in sql
        self.calls += 1
        if self.calls == 1:
            return [_persisted_item(index) for index in range(200)]
        if self.calls == 2:
            return [
                *[_persisted_item(index) for index in range(200, 250)],
                _persisted_item(250, eligible=True),
            ]
        return []


@pytest.mark.asyncio
async def test_technical_items_do_not_consume_persisted_business_limit():
    conn = ItemPages()

    items = await fetch_eligible_persisted_items(
        conn,
        workspace_id=WORKSPACE_ID,
        tenant_id=None,
        owner_id=None,
        kinds=("intelligence_signal", "source_state"),
        row_to_item=normalize_persisted_business_item,
        limit=1,
        page_size=200,
    )

    assert [item["id"] for item in items] == ["item-0250"]
    assert conn.calls == 2


class ParentAndChildPage:
    def __init__(self) -> None:
        self.page_calls = 0
        self.parent = _persisted_item(1, eligible=True)
        self.parent["item_kind"] = "anomaly"
        self.child = _persisted_item(2, eligible=True)
        self.child["metadata"] = {
            "parent_item_id": self.parent["item_id"],
            "evidence_refs": ["evidence:child"],
        }

    async def fetch(self, sql: str, *_args):
        if "WITH RECURSIVE lineage" in sql:
            return [self.parent]
        self.page_calls += 1
        return [self.parent, self.child] if self.page_calls == 1 else []


@pytest.mark.asyncio
async def test_lineage_seed_duplicate_does_not_hide_parent_and_child():
    conn = ParentAndChildPage()

    items = await fetch_eligible_persisted_items(
        conn,
        workspace_id=WORKSPACE_ID,
        tenant_id=None,
        owner_id=None,
        kinds=(),
        row_to_item=normalize_persisted_business_item,
        limit=2,
        page_size=200,
    )

    assert [item["id"] for item in items] == ["item-0001", "item-0002"]


class ExhaustiveItemPages:
    def __init__(self) -> None:
        self.rows = [_persisted_item(index, eligible=True) for index in range(5001)]
        self.calls = 0

    async def fetch(self, sql: str, *_args):
        assert "FROM control_room_items" in sql
        start = self.calls * 250
        self.calls += 1
        return self.rows[start : start + 250]


@pytest.mark.asyncio
async def test_unbounded_projection_exhausts_more_than_five_thousand_items():
    conn = ExhaustiveItemPages()

    items = await fetch_eligible_persisted_items(
        conn,
        workspace_id=WORKSPACE_ID,
        tenant_id=None,
        owner_id=None,
        kinds=(),
        row_to_item=normalize_persisted_business_item,
        limit=None,
        page_size=250,
    )

    assert len(items) == 5001
    assert items[-1]["id"] == "item-5000"
    assert conn.calls == 21


class DecisionPages:
    def __init__(self) -> None:
        origin = datetime(2026, 7, 16, tzinfo=UTC)
        self.rows = [
            {
                "id": index,
                "workspace_id": WORKSPACE_ID,
                "created_at": origin - timedelta(seconds=index),
                "status": "open",
                "kpis": [],
            }
            for index in range(1, 5002)
        ]
        self.page_calls = 0

    async def fetch(self, sql: str, *args):
        normalized = " ".join(sql.split())
        if "FROM control_room_items" in normalized:
            return []
        if "FROM decision_actions" in normalized:
            return [
                {"decision_id": decision_id}
                for decision_id in args[1]
                if int(decision_id) != 5001
            ]
        self.page_calls += 1
        start = (self.page_calls - 1) * 500
        return self.rows[start : start + 500]


@pytest.mark.asyncio
async def test_valid_decision_after_five_thousand_technical_rows_is_returned():
    conn = DecisionPages()

    rows = await fetch_business_decisions(
        conn,
        sql=(
            "SELECT * FROM decisions WHERE workspace_id = $1 "
            "ORDER BY created_at DESC, id DESC"
        ),
        params=[WORKSPACE_ID],
        workspace_id=WORKSPACE_ID,
        limit=1,
    )

    assert [row["id"] for row in rows] == [5001]
    assert conn.page_calls == 11
