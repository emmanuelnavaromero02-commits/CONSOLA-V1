from __future__ import annotations

import pytest

from app.services import control_room_service
from app.services.control_room.business_eligibility import (
    EligibilityReason,
    classify_business_item,
)
from app.services.control_room.business_lineage import MAX_LINEAGE_DEPTH
from app.services.control_room.business_projection import filter_business_items
from app.services.control_room.business_repository import fetch_lineage_rows


def _business(item_id: str = "root", **overrides):
    item = {
        "id": item_id,
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "data_status": "ready",
        "observation_date": "2026-07-16",
        "observed_value": 1,
        "evidence_refs": [f"evidence:{item_id}"],
    }
    return {**item, **overrides}


def _derived(item_id: str, **overrides):
    return _business(item_id, kind="intelligence_signal", **overrides)


@pytest.mark.parametrize(
    "reference",
    [
        {"parent_item_id": "root"},
        {"source_item_id": "root"},
        {"derived_from": "root"},
        {"lineage": {"parent_item_id": "root"}},
        {"lineage": {"source_item_id": "root"}},
    ],
)
def test_all_canonical_parent_fields_require_an_eligible_parent(reference):
    child = _derived("child", **reference)

    assert classify_business_item(child).reason is EligibilityReason.INELIGIBLE_PARENT
    assert classify_business_item(
        child,
        eligible_parent_ids={"root"},
    ).eligible


def test_source_dataset_does_not_bypass_explicit_missing_parent():
    child = _derived("child", parent_item_id="missing")

    assert filter_business_items([_business(), child]) == [_business()]


def test_complete_derived_chain_resolves_to_business_root():
    root = _business()
    middle = _derived("middle", parent_item_id="root")
    child = _derived("child", source_item_id="middle")

    assert {item["id"] for item in filter_business_items([child, middle, root])} == {
        "root",
        "middle",
        "child",
    }


def test_chain_resolving_to_source_state_is_rejected():
    root = _business(kind="source_state")
    middle = _derived("middle", parent_item_id="root")
    child = _derived("child", source_item_id="middle")

    assert filter_business_items([child, middle, root]) == []


def test_every_explicit_parent_must_exist_and_be_eligible():
    child = _derived(
        "child",
        parent_item_id="root",
        source_item_id="missing",
    )

    assert [item["id"] for item in filter_business_items([_business(), child])] == [
        "root"
    ]


def test_derived_mapping_with_conflicting_ids_requires_both_parents():
    child = _derived(
        "child",
        derived_from={"item_id": "root", "id": "diagnostic"},
    )
    root = _business()
    diagnostic = _business("diagnostic", kind="source_state")

    assert [
        item["id"] for item in filter_business_items([root, diagnostic, child])
    ] == ["root"]


def test_persisted_metadata_item_kind_cannot_hide_source_state():
    item = {
        "item_id": "signal-1",
        "item_kind": "intelligence_signal",
        "source_dataset": "gold_metrics",
        "metadata": {
            "item_kind": "source_state",
            "data_status": "ready",
            "observation_date": "2026-07-16",
            "observed_value": 1,
            "evidence_refs": ["evidence:signal-1"],
        },
    }

    normalized = control_room_service._persisted_intelligence_payload(item)

    assert classify_business_item(normalized).reason is EligibilityReason.SOURCE_STATE


def test_cycles_and_duplicate_ids_fail_closed():
    cycle_a = _derived("a", parent_item_id="b")
    cycle_b = _derived("b", parent_item_id="a")
    duplicate_a = _business("duplicate")
    duplicate_b = _business("duplicate")

    assert filter_business_items([cycle_a, cycle_b]) == []
    assert filter_business_items([duplicate_a, duplicate_b]) == []


def test_lineage_over_maximum_depth_fails_closed():
    root = _business()
    chain = [root]
    parent_id = root["id"]
    for index in range(MAX_LINEAGE_DEPTH + 1):
        child = _derived(f"child-{index}", parent_item_id=parent_id)
        chain.append(child)
        parent_id = child["id"]

    eligible_ids = {item["id"] for item in filter_business_items(reversed(chain))}

    assert root["id"] in eligible_ids
    assert chain[-1]["id"] not in eligible_ids


@pytest.mark.parametrize(
    "malformed",
    [
        {"parent_item_id": None},
        {"source_item_id": ""},
        {"derived_from": 42},
        {"lineage": "root"},
        {"lineage": {"parent_item_id": None}},
    ],
)
def test_malformed_derived_lineage_fails_closed(malformed):
    result = classify_business_item(_derived("child", **malformed))

    assert result.reason is EligibilityReason.INVALID_LINEAGE


def test_derived_item_with_source_dataset_but_no_evidence_has_no_root_lineage():
    child = _derived("child", evidence_refs=[])

    assert classify_business_item(child).reason is EligibilityReason.MISSING_LINEAGE


def test_contradictory_kind_and_item_kind_source_state_is_diagnostic():
    item = _business(kind="anomaly", item_kind="source_state")

    assert classify_business_item(item).reason is EligibilityReason.SOURCE_STATE


class LineageConnection:
    def __init__(self) -> None:
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append((" ".join(sql.split()), args))
        return []


@pytest.mark.asyncio
async def test_lineage_repository_uses_one_recursive_scoped_query():
    conn = LineageConnection()

    await fetch_lineage_rows(
        conn,
        workspace_id="workspace-1",
        tenant_id="tenant-1",
        owner_id=7,
        parent_ids=["parent-1", "parent-2"],
    )

    assert len(conn.calls) == 1
    sql, args = conn.calls[0]
    assert "WITH RECURSIVE lineage" in sql
    assert "jsonb_array_elements" in sql
    assert "entry.value->>'item_id'" in sql
    assert "parent_item_id" in sql
    assert "source_item_id" in sql
    assert "derived_from" in sql
    assert "seed.tenant_id::text = $3" in sql
    assert "seed.owner_user_id = $4" in sql
    assert args[:4] == (
        "workspace-1",
        ["parent-1", "parent-2"],
        "tenant-1",
        7,
    )
