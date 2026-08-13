"""Fase 7 — relationship (FK) candidate discovery from profiler stats."""
from __future__ import annotations

from refinement.app.relationship_discovery import discover_relationship_candidates


def _col(dataset, name, dtype, distinct, null_rate=0.0):
    return {
        "dataset": dataset,
        "column_name": name,
        "data_type": dtype,
        "distinct_count": distinct,
        "null_rate": null_rate,
    }


def test_proposes_fk_when_child_points_at_unique_key():
    columns = [
        # parent: user_id is a unique, non-null key (distinct == row_count)
        _col("gold_users", "user_id", "VARCHAR", 10, 0.0),
        # child: user_id is non-unique -> a many-to-one FK candidate
        _col("gold_orders", "user_id", "VARCHAR", 4, 0.0),
    ]
    row_counts = {"gold_users": 10, "gold_orders": 25}
    out = discover_relationship_candidates(columns, row_counts)
    assert len(out) == 1
    c = out[0]
    assert (c["from_dataset"], c["from_column"]) == ("gold_orders", "user_id")
    assert (c["to_dataset"], c["to_column"]) == ("gold_users", "user_id")
    assert c["join_hint"] == "many_to_one"
    assert c["confidence"] >= 0.8  # exact name + child not itself a key
    assert c["status"] == "candidate"


def test_name_normalization_matches_camel_and_snake():
    columns = [
        _col("gold_users", "userId", "VARCHAR", 10, 0.0),      # key (camelCase)
        _col("gold_orders", "user_id", "VARCHAR", 4, 0.0),     # child (snake_case)
    ]
    row_counts = {"gold_users": 10, "gold_orders": 25}
    out = discover_relationship_candidates(columns, row_counts)
    assert len(out) == 1
    assert out[0]["to_column"] == "userId"


def test_cardinality_infeasible_child_is_rejected():
    # child has MORE distinct values than the key -> cannot be a FK to it
    columns = [
        _col("gold_users", "user_id", "VARCHAR", 10, 0.0),   # key: 10 distinct
        _col("gold_orders", "user_id", "VARCHAR", 20, 0.0),  # child: 20 distinct
    ]
    row_counts = {"gold_users": 10, "gold_orders": 30}
    assert discover_relationship_candidates(columns, row_counts) == []


def test_type_mismatch_is_rejected():
    columns = [
        _col("gold_users", "user_id", "BIGINT", 10, 0.0),    # key int
        _col("gold_orders", "user_id", "VARCHAR", 4, 0.0),   # child text
    ]
    row_counts = {"gold_users": 10, "gold_orders": 25}
    assert discover_relationship_candidates(columns, row_counts) == []


def test_non_unique_target_is_not_a_key():
    columns = [
        _col("gold_users", "user_id", "VARCHAR", 8, 0.0),    # NOT unique (8 != 10)
        _col("gold_orders", "user_id", "VARCHAR", 4, 0.0),
    ]
    row_counts = {"gold_users": 10, "gold_orders": 25}
    assert discover_relationship_candidates(columns, row_counts) == []


def test_nullable_unique_column_is_not_a_key():
    columns = [
        _col("gold_users", "user_id", "VARCHAR", 10, 0.1),   # unique but has nulls
        _col("gold_orders", "user_id", "VARCHAR", 4, 0.0),
    ]
    row_counts = {"gold_users": 10, "gold_orders": 25}
    assert discover_relationship_candidates(columns, row_counts) == []


def test_same_dataset_is_not_proposed():
    columns = [
        _col("gold_users", "user_id", "VARCHAR", 10, 0.0),   # key
        _col("gold_users", "user_id", "VARCHAR", 10, 0.0),   # itself
    ]
    row_counts = {"gold_users": 10}
    assert discover_relationship_candidates(columns, row_counts) == []


def test_trivial_single_value_key_is_ignored():
    # a "unique" column over a 1-row table is not a useful join target
    columns = [
        _col("gold_flag", "id", "VARCHAR", 1, 0.0),
        _col("gold_orders", "id", "VARCHAR", 1, 0.0),
    ]
    row_counts = {"gold_flag": 1, "gold_orders": 1}
    assert discover_relationship_candidates(columns, row_counts) == []


def test_unprofiled_columns_produce_no_candidates():
    # distinct_count None (never profiled) -> cannot validate a key
    columns = [
        _col("gold_users", "user_id", "VARCHAR", None, None),
        _col("gold_orders", "user_id", "VARCHAR", None, None),
    ]
    row_counts = {"gold_users": 10, "gold_orders": 25}
    assert discover_relationship_candidates(columns, row_counts) == []
