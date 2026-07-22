from __future__ import annotations

import asyncio
import json

import pytest

from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.control_room.business_item_persistence_sql import (
    ENSURE_ITEM_SQL,
    PERSIST_ITEMS_SQL,
)
from app.services.control_room.business_observation_order import (
    OBSERVATION_ORDER_BASELINE_KEY,
    OBSERVATION_ORDER_KEY,
    OBSERVATION_ORDER_VERSION,
    business_observation_order,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
)
from app.services.control_room.business_workflow_reconciliation import (
    workflow_metadata_patches,
)


def _row(value: int, observed_at: str, *, title: str) -> dict:
    return {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "owner_user_id": 7,
        "item_id": "business-1",
        "id": "business-1",
        "item_kind": "anomaly",
        "kind": "anomaly",
        "cartridge_id": "sap_hcm",
        "source_dataset": "gold_people",
        "title": title,
        "metric_type": "count",
        "observed_value": value,
        "population_count": 10,
        "observation_date": observed_at,
        "metadata": {OBSERVATION_ORDER_KEY: "client-forged"},
    }


def test_observation_order_prefers_source_time_then_deterministic_fingerprint():
    old = _row(1, "2026-07-20T10:00:00Z", title="Old")
    new = _row(2, "2026-07-20T18:00:00+00:00", title="New")
    corrected_a = _row(3, "2026-07-21", title="Correction A")
    corrected_b = _row(4, "2026-07-21", title="Correction B")

    assert business_observation_order(old) < business_observation_order(new)
    assert business_observation_order(corrected_a) != business_observation_order(
        corrected_b
    )
    assert max(
        business_observation_order(corrected_a),
        business_observation_order(corrected_b),
    ) == max(
        business_observation_order(corrected_b),
        business_observation_order(corrected_a),
    )


def test_refresh_generated_at_never_outranks_an_authoritative_observation_date():
    stale = {
        **_row(1, "2026-07-20", title="Stale"),
        "generated_at": "2026-07-22T18:00:00Z",
    }
    current = {
        **_row(2, "2026-07-21", title="Current"),
        "generated_at": "2026-07-21T10:00:00Z",
    }

    assert business_observation_order(stale) < business_observation_order(current)


class _ConvergentConnection:
    def __init__(self) -> None:
        self.old_arrived = asyncio.Event()
        self.new_committed = asyncio.Event()
        self.lock = asyncio.Lock()
        self.current: dict | None = None

    async def execute(self, _sql: str, payload: str, *_args):
        row = json.loads(payload)[0]
        if row["title"] == "Old":
            self.old_arrived.set()
            await self.new_committed.wait()
        async with self.lock:
            incoming = row["metadata"][OBSERVATION_ORDER_KEY]
            current = (
                self.current["metadata"][OBSERVATION_ORDER_KEY] if self.current else ""
            )
            if incoming >= current:
                self.current = row
        if row["title"] == "New":
            self.new_committed.set()
        return "INSERT 0 1"


class _LegacyConnection:
    def __init__(self, existing: dict) -> None:
        self.existing = existing
        self.payload: list[dict] = []

    async def fetch(self, *_args):
        return [self.existing]

    async def execute(self, _sql: str, payload: str, *_args):
        self.payload = json.loads(payload)
        return "INSERT 0 1"


class _WorkflowConnection:
    def __init__(self, existing: dict) -> None:
        self.existing = existing

    async def fetch(self, *_args):
        return [self.existing]


@pytest.mark.asyncio
async def test_older_refresh_finishing_last_converges_to_newer_generation():
    conn = _ConvergentConnection()
    old = _row(1, "2026-07-20T10:00:00Z", title="Old")
    new = _row(2, "2026-07-20T18:00:00Z", title="New")

    old_task = asyncio.create_task(persist_item_rows(conn, [old], owner_scope_id=7))
    await conn.old_arrived.wait()
    await persist_item_rows(conn, [new], owner_scope_id=7)
    await old_task

    assert conn.current is not None
    assert conn.current["title"] == "New"
    token = conn.current["metadata"][OBSERVATION_ORDER_KEY]
    assert token.startswith(f"{OBSERVATION_ORDER_VERSION:04d}|")
    assert token != "client-forged"


@pytest.mark.asyncio
async def test_legacy_row_supplies_server_computed_order_baseline():
    existing = _row(2, "2026-07-20T18:00:00Z", title="Current")
    existing["status"] = "open"
    existing["execution_status"] = "not_started"
    conn = _LegacyConnection(existing)

    await persist_item_rows(
        conn,
        [_row(1, "2026-07-20T10:00:00Z", title="Old")],
        owner_scope_id=7,
    )

    metadata = conn.payload[0]["metadata"]
    assert metadata[OBSERVATION_ORDER_BASELINE_KEY] == (
        business_observation_order(existing)
    )
    assert metadata[OBSERVATION_ORDER_KEY] != "client-forged"


@pytest.mark.asyncio
async def test_reconciliation_rejects_same_time_losing_fingerprint_before_patch():
    candidates = (
        _row(3, "2026-07-21", title="Correction A"),
        _row(4, "2026-07-21", title="Correction B"),
    )
    loser, winner = sorted(candidates, key=business_observation_order)
    loser_order = business_observation_order(loser)
    winner_order = business_observation_order(winner)
    assert loser_order.split("|", 2)[1] == winner_order.split("|", 2)[1]
    assert loser_order < winner_order
    existing = {
        **winner,
        "decision_id": 42,
        "status": "decision_created",
        "selected_option_id": "review",
        "execution_status": "dry_run_validated",
        "metadata": {
            OBSERVATION_ORDER_KEY: winner_order,
            CURRENT_ELIGIBILITY_FINGERPRINT_KEY: "winner",
        },
    }

    patches = await workflow_metadata_patches(_WorkflowConnection(existing), [loser])

    assert patches == {}


def test_upserts_guard_semantics_and_workflow_with_the_same_order_token():
    for statement in (PERSIST_ITEMS_SQL, ENSURE_ITEM_SQL):
        sql = " ".join(statement.split())
        order = f"metadata->>'{OBSERVATION_ORDER_KEY}'"
        assert sql.count(order) >= 10
        assert OBSERVATION_ORDER_BASELINE_KEY in sql
        assert "jsonb_set(control_room_items.metadata" in sql
        assert "THEN control_room_items.status" in sql
        assert "THEN control_room_items.decision_id" in sql
        assert "THEN control_room_items.selected_option_id" in sql
        assert "THEN control_room_items.execution_status" in sql
        assert "ELSE control_room_items.last_seen_at END" in sql
        assignments = (
            ("metadata = CASE", "impact_estimate ="),
            ("status = CASE", "decision_id = CASE"),
            ("decision_id = CASE", "selected_option_id = CASE"),
            ("selected_option_id = CASE", "execution_status = CASE"),
            ("execution_status = CASE", "WHERE $"),
        )
        for start, end in assignments:
            case = sql.split(start, 1)[1].split(end, 1)[0]
            assert case.index("WHEN NOT (") < case.index("workflow_quarantine")


def test_ensure_and_persist_status_keep_current_branch_after_stale_guard():
    ensure_sql = " ".join(ENSURE_ITEM_SQL.split())
    persist_sql = " ".join(PERSIST_ITEMS_SQL.split())
    ensure_status = ensure_sql.split("status = CASE", 1)[1].split(
        "decision_id = CASE", 1
    )[0]
    persist_status = persist_sql.split("status = CASE", 1)[1].split(
        "decision_id = CASE", 1
    )[0]

    assert "ELSE EXCLUDED.status END" in ensure_status
    assert f"metadata->>'{OBSERVATION_ORDER_KEY}'" in ensure_status
    assert f"metadata->>'{OBSERVATION_ORDER_KEY}'" in persist_status
    assert "ELSE control_room_items.status END" in persist_status
