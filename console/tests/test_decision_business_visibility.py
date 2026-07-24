from datetime import UTC, datetime, timedelta

import pytest

from app.domains.decisions.business_visibility import (
    fetch_business_decisions,
    preserve_control_room_provenance,
)
from app.services.control_room.business_projection import (
    filter_business_decisions,
    normalize_persisted_business_item,
)
from app.services.control_room import business_runtime_evidence as evidence
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    ELIGIBILITY_POLICY_VERSION,
    business_observation_fingerprint,
)


def _eligible_metadata(item_id: str, **updates) -> dict:
    metadata = {
        "data_status": "ready",
        "metric_type": "scalar",
        "observed_value": 1,
        "observation_date": "2026-07-17T10:00:00Z",
        "source_system": "sap",
        "cartridge": "sap",
        **updates,
    }
    metadata |= evidence.runtime_row_evidence_fields(
        source_dataset="gold_a",
        source_system="sap",
        cartridge="sap",
        tenant_id="tenant-A",
        workspace_id="workspace-A",
        source_row={"item_id": item_id, **metadata},
        locator_field="item_id",
        observed_at="2026-07-17T10:00:00Z",
    )
    return metadata


def _with_eligible_provenance(row):
    row = {**row, "tenant_id": "tenant-A", "workspace_id": "workspace-A"}
    item = normalize_persisted_business_item(row)
    fingerprint = business_observation_fingerprint(item)
    metadata = dict(item.get("metadata") or {})
    metadata[DECISION_PROVENANCE_KEY] = {
        "policy_version": ELIGIBILITY_POLICY_VERSION,
        "stage": "decision_created",
        "workspace_id": "workspace-A",
        "eligible_at_link": True,
        "decision_id": str(row["decision_id"]),
        "item_id": item["id"],
        "kind": item["kind"],
        "fingerprint": fingerprint,
    }
    return {**row, "metadata": metadata}


def test_historical_decisions_are_filtered_through_linked_business_items():
    decisions = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]
    linked = [
        {
            "decision_id": 1,
            "item_id": "diagnostic",
            "item_kind": "source_state",
            "source_dataset": "gold_a",
            "metadata": {},
        },
        {
            "decision_id": 2,
            "item_id": "derived",
            "item_kind": "agent_alert",
            "source_dataset": "gold_a",
            "metadata": '{"parent_item_id":"technical-parent"}',
        },
        _with_eligible_provenance(
            {
                "decision_id": 3,
                "item_id": "business",
                "item_kind": "anomaly",
                "source_dataset": "gold_a",
                "metadata": _eligible_metadata("business"),
            }
        ),
    ]
    lineage = [
        {
            "item_id": "technical-parent",
            "item_kind": "source_state",
            "source_dataset": "gold_a",
            "metadata": {"data_status": "missing"},
        }
    ]

    assert filter_business_decisions(
        decisions,
        linked,
        lineage_items=lineage,
    ) == [{"id": 3}, {"id": 4}]


def test_decision_linked_to_business_and_diagnostic_items_fails_closed():
    decision = {"id": 1}
    linked = [
        {
            "decision_id": 1,
            "item_id": "business",
            "item_kind": "anomaly",
            "source_dataset": "gold_a",
            "metadata": _eligible_metadata("business"),
        },
        {
            "decision_id": 1,
            "item_id": "diagnostic",
            "item_kind": "source_state",
            "source_dataset": "gold_a",
            "metadata": {"data_status": "missing"},
        },
    ]

    assert filter_business_decisions([decision], linked) == []


def test_lineage_row_duplicated_by_linked_seed_does_not_hide_valid_decision():
    decision = {"id": 1}
    root = _with_eligible_provenance(
        {
            "decision_id": 1,
            "item_id": "root",
            "item_kind": "anomaly",
            "source_dataset": "gold_a",
            "metadata": _eligible_metadata("root"),
        }
    )
    child = _with_eligible_provenance(
        {
            "decision_id": 1,
            "item_id": "child",
            "item_kind": "intelligence_signal",
            "source_dataset": "gold_a",
            "metadata": _eligible_metadata("child", parent_item_id="root"),
        }
    )

    assert filter_business_decisions(
        [decision], [root, child], lineage_items=[root]
    ) == [decision]


def test_kpi_patch_removes_control_room_provenance_without_server_link_proof():
    marker = {"provenance": {"origin": "control_room", "item_id": "item-1"}}

    assert preserve_control_room_provenance([marker], []) == []
    assert (
        preserve_control_room_provenance([{"provenance": {"origin": "manual"}}], [])
        == []
    )


class DecisionBatchConnection:
    def __init__(self) -> None:
        self.decision_fetches = 0
        self.link_fetches = 0
        self.origin_fetches = 0
        self.origin_ids: set[int] = set()
        self.calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql: str, *args):
        normalized = " ".join(sql.split()).upper()
        self.calls.append((normalized, args))
        if "FROM CONTROL_ROOM_ITEMS" in normalized:
            self.link_fetches += 1
            return []
        if "FROM DECISION_ACTIONS" in normalized:
            self.origin_fetches += 1
            requested = {int(value) for value in args[1]}
            return [
                {"decision_id": decision_id}
                for decision_id in sorted(self.origin_ids & requested)
            ]
        self.decision_fetches += 1
        if self.decision_fetches == 1:
            return [
                {
                    "id": index,
                    "created_at": datetime(2026, 7, 17, tzinfo=UTC),
                    "kpis": [{"provenance": {"origin": "control_room"}}],
                }
                for index in range(1, 501)
            ]
        if self.decision_fetches == 2:
            return [
                {
                    "id": index,
                    "created_at": datetime(2026, 7, 16, tzinfo=UTC),
                    "kpis": [{"provenance": {"origin": "manual"}}],
                }
                for index in range(501, 1001)
            ]
        return []


@pytest.mark.asyncio
async def test_linked_decision_lookup_uses_explicit_tenant_and_workspace_scope():
    conn = DecisionBatchConnection()

    await fetch_business_decisions(
        conn,
        sql="SELECT * FROM decisions ORDER BY created_at DESC, id DESC",
        params=[],
        workspace_id="workspace-1",
        tenant_id="tenant-1",
        limit=1,
    )

    linked_sql, linked_args = next(
        (sql, args) for sql, args in conn.calls if "FROM CONTROL_ROOM_ITEMS" in sql
    )
    assert "WORKSPACE_ID = $1" in linked_sql
    assert "TENANT_ID::TEXT = $3" in linked_sql
    assert linked_args[0] == "workspace-1"
    assert linked_args[2] == "tenant-1"


@pytest.mark.asyncio
async def test_decision_limit_is_applied_after_business_filter_in_bounded_batches():
    conn = DecisionBatchConnection()

    rows = await fetch_business_decisions(
        conn,
        sql="SELECT * FROM decisions ORDER BY created_at DESC, id DESC",
        params=[],
        workspace_id="workspace-1",
    )

    assert [row["id"] for row in rows] == list(range(501, 1001))
    assert conn.decision_fetches == 2
    assert conn.link_fetches == 2
    assert conn.origin_fetches == 2
    assert all("OFFSET" not in sql for sql, _args in conn.calls)


@pytest.mark.asyncio
async def test_immutable_action_origin_hides_control_room_orphan_after_kpi_edit():
    conn = DecisionBatchConnection()
    conn.origin_ids = set(range(501, 1001))

    rows = await fetch_business_decisions(
        conn,
        sql="SELECT * FROM decisions ORDER BY created_at DESC, id DESC",
        params=[],
        workspace_id="workspace-1",
    )

    assert rows == []


class AllTechnicalConnection(DecisionBatchConnection):
    async def fetch(self, sql: str, *args):
        normalized = " ".join(sql.split()).upper()
        self.calls.append((normalized, args))
        if "FROM CONTROL_ROOM_ITEMS" in normalized:
            self.link_fetches += 1
            return []
        if "FROM DECISION_ACTIONS" in normalized:
            self.origin_fetches += 1
            return []
        self.decision_fetches += 1
        if self.decision_fetches > 10:
            return []
        offset = (self.decision_fetches - 1) * 500
        return [
            {
                "id": offset + index,
                "created_at": datetime(2026, 7, 17, tzinfo=UTC)
                - timedelta(days=self.decision_fetches),
                "kpis": [{"provenance": {"origin": "control_room"}}],
            }
            for index in range(1, 501)
        ]


@pytest.mark.asyncio
async def test_decision_scan_is_keyset_paginated_until_exhausted():
    conn = AllTechnicalConnection()
    rows = await fetch_business_decisions(
        conn,
        sql="SELECT * FROM decisions ORDER BY created_at DESC, id DESC",
        params=[],
        workspace_id="workspace-1",
    )

    assert rows == []
    assert conn.decision_fetches == 11
    assert conn.link_fetches == 10
    assert conn.origin_fetches == 10
    assert all("OFFSET" not in sql for sql, _args in conn.calls)
