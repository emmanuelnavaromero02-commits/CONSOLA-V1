from __future__ import annotations

from random import Random

import pytest

from app.services.control_room.business_eligibility import (
    EligibilityReason,
    classify_business_item,
)
from app.services.control_room.business_lineage import MAX_LINEAGE_DEPTH
from app.services.control_room.business_projection import filter_business_items
from app.services.control_room.business_resolution import resolve_business_lineage


def _root(item_id: str = "node-0", **overrides):
    item = {
        "id": item_id,
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "data_status": "ready",
        "observation_date": "2026-07-17",
        "observed_value": 1,
        "evidence_refs": [f"evidence:{item_id}"],
    }
    return {**item, **overrides}


def _derived(item_id: str, parent_id: str | None = None, **overrides):
    reference = {"parent_item_id": parent_id} if parent_id is not None else {}
    return _root(
        item_id,
        kind="intelligence_signal",
        **reference,
        **overrides,
    )


def _chain(edge_count: int):
    rows = [_root()]
    for index in range(1, edge_count + 1):
        rows.append(_derived(f"node-{index}", rows[-1]["id"]))
    return rows


def _ordered(rows, ordering: str):
    result = list(rows)
    if ordering == "reverse":
        result.reverse()
    elif ordering == "random":
        Random(17).shuffle(result)
    return result


@pytest.mark.parametrize("ordering", ["normal", "reverse", "random"])
def test_long_chain_depth_is_independent_of_input_order(ordering):
    chain = _chain(MAX_LINEAGE_DEPTH + 4)
    rows = _ordered(chain, ordering)
    resolutions = resolve_business_lineage(rows)
    by_id = dict(zip((row["id"] for row in rows), resolutions, strict=True))

    assert {row["id"] for row in filter_business_items(rows)} == {
        row["id"] for row in chain[: MAX_LINEAGE_DEPTH + 1]
    }
    assert by_id[f"node-{MAX_LINEAGE_DEPTH}"].depth == MAX_LINEAGE_DEPTH
    assert by_id[f"node-{MAX_LINEAGE_DEPTH}"].eligible
    assert by_id[f"node-{MAX_LINEAGE_DEPTH + 1}"].depth == (MAX_LINEAGE_DEPTH + 1)
    assert by_id[f"node-{MAX_LINEAGE_DEPTH + 1}"].cause == "maximum_depth"
    assert by_id[f"node-{MAX_LINEAGE_DEPTH + 4}"].depth == (MAX_LINEAGE_DEPTH + 4)
    assert by_id[f"node-{MAX_LINEAGE_DEPTH + 4}"].cause == "maximum_depth"


def test_exact_depth_boundary_is_eligible():
    chain = _chain(MAX_LINEAGE_DEPTH)

    assert [row["id"] for row in filter_business_items(chain)] == [
        row["id"] for row in chain
    ]


def test_projected_depth_context_cannot_reset_the_boundary():
    current = filter_business_items([_root()])[0]
    for index in range(1, MAX_LINEAGE_DEPTH + 1):
        child = _derived(f"node-{index}", current["id"])
        current = filter_business_items([current, child])[-1]

    over_boundary = _derived("over-boundary", current["id"])

    assert [row["id"] for row in filter_business_items([current, over_boundary])] == [
        current["id"]
    ]


@pytest.mark.parametrize("ordering", ["normal", "reverse", "random"])
def test_cycles_missing_parents_and_duplicates_are_order_independent(ordering):
    rows = [
        _root("root"),
        _derived("cycle-a", "cycle-b"),
        _derived("cycle-b", "cycle-a"),
        _derived("cycle-child", "cycle-a"),
        _derived("missing-child", "absent"),
        _root("duplicate"),
        _root("duplicate"),
        _derived("duplicate-child", "duplicate"),
    ]

    assert [row["id"] for row in filter_business_items(_ordered(rows, ordering))] == [
        "root"
    ]


def test_structural_failure_causes_are_retained():
    rows = [
        _derived("cycle-a", "cycle-b"),
        _derived("cycle-b", "cycle-a"),
        _derived("missing-child", "absent"),
        _root("duplicate"),
        _root("duplicate"),
        _derived("duplicate-child", "duplicate"),
    ]
    results = resolve_business_lineage(rows)

    assert results[0].cause == "cycle"
    assert results[1].cause == "cycle"
    assert results[2].cause == "missing_parent"
    assert results[3].cause == "duplicate_id"
    assert results[4].cause == "duplicate_id"
    assert results[5].cause == "duplicate_parent"


@pytest.mark.parametrize(
    "invalid",
    [
        {"derived_from": []},
        {"derived_from": ()},
        {"derived_from": {}},
        {"lineage": []},
        {"lineage": ()},
        {"lineage": {}},
        {"lineage": {"parent_item_id": []}},
    ],
)
def test_explicit_empty_derivation_references_fail_closed(invalid):
    result = classify_business_item(_derived("child", **invalid))

    assert result.reason is EligibilityReason.INVALID_LINEAGE


def test_derived_root_without_reference_uses_dataset_and_evidence():
    root = _derived("derived-root")

    assert classify_business_item(root).eligible
    assert filter_business_items([root]) == [root]


@pytest.mark.parametrize(
    "references",
    [
        {
            "parent_item_id": "root",
            "metadata": {"parent_item_id": "other"},
        },
        {
            "source_item_id": "root",
            "metadata": {"lineage": {"source_item_id": "other"}},
        },
        {
            "derived_from": "root",
            "details": {"derived_from": "other"},
        },
    ],
)
def test_contradictory_references_across_semantic_maps_fail_closed(references):
    result = classify_business_item(
        _derived("child", **references),
        eligible_parent_ids={"root", "other"},
    )

    assert result.reason is EligibilityReason.INVALID_LINEAGE


def test_matching_references_across_semantic_maps_remain_valid():
    root = _root("root")
    child = _derived(
        "child",
        "root",
        metadata={"lineage": {"parent_item_id": "root"}},
    )

    assert [row["id"] for row in filter_business_items([child, root])] == [
        "child",
        "root",
    ]
