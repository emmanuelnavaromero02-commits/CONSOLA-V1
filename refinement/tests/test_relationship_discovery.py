from __future__ import annotations

import duckdb
import pytest

from refinement.app.duckdb_engine import DuckDBEngine
from refinement.app.relationship_discovery import discover_relationship_candidates


def _engine_over(con):
    eng = DuckDBEngine()
    eng._con = con
    eng._pg_gold_attach = lambda c, uc=None: "pggold"
    return eng


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
        _col("gold_users", "user_id", "VARCHAR", 10, 0.0),
        _col("gold_orders", "user_id", "VARCHAR", 4, 0.0),
    ]
    row_counts = {"gold_users": 10, "gold_orders": 25}
    out = discover_relationship_candidates(columns, row_counts)
    assert len(out) == 1
    c = out[0]
    assert (c["from_dataset"], c["from_column"]) == ("gold_orders", "user_id")
    assert (c["to_dataset"], c["to_column"]) == ("gold_users", "user_id")
    assert c["join_hint"] == "many_to_one"
    assert c["confidence"] >= 0.8
    assert c["status"] == "candidate"


def test_name_normalization_matches_camel_and_snake():
    columns = [
        _col("gold_users", "userId", "VARCHAR", 10, 0.0),
        _col("gold_orders", "user_id", "VARCHAR", 4, 0.0),
    ]
    row_counts = {"gold_users": 10, "gold_orders": 25}
    out = discover_relationship_candidates(columns, row_counts)
    assert len(out) == 1
    assert out[0]["to_column"] == "userId"


def test_cardinality_infeasible_child_is_rejected():
    columns = [
        _col("gold_users", "user_id", "VARCHAR", 10, 0.0),
        _col("gold_orders", "user_id", "VARCHAR", 20, 0.0),
    ]
    row_counts = {"gold_users": 10, "gold_orders": 30}
    assert discover_relationship_candidates(columns, row_counts) == []


def test_type_mismatch_is_rejected():
    columns = [
        _col("gold_users", "user_id", "BIGINT", 10, 0.0),
        _col("gold_orders", "user_id", "VARCHAR", 4, 0.0),
    ]
    row_counts = {"gold_users": 10, "gold_orders": 25}
    assert discover_relationship_candidates(columns, row_counts) == []


def test_non_unique_target_is_not_a_key():
    columns = [
        _col("gold_users", "user_id", "VARCHAR", 8, 0.0),
        _col("gold_orders", "user_id", "VARCHAR", 4, 0.0),
    ]
    row_counts = {"gold_users": 10, "gold_orders": 25}
    assert discover_relationship_candidates(columns, row_counts) == []


def test_nullable_unique_column_is_not_a_key():
    columns = [
        _col("gold_users", "user_id", "VARCHAR", 10, 0.1),
        _col("gold_orders", "user_id", "VARCHAR", 4, 0.0),
    ]
    row_counts = {"gold_users": 10, "gold_orders": 25}
    assert discover_relationship_candidates(columns, row_counts) == []


def test_same_dataset_is_not_proposed():
    columns = [
        _col("gold_users", "user_id", "VARCHAR", 10, 0.0),
        _col("gold_users", "user_id", "VARCHAR", 10, 0.0),
    ]
    row_counts = {"gold_users": 10}
    assert discover_relationship_candidates(columns, row_counts) == []


def test_trivial_single_value_key_is_ignored():
    columns = [
        _col("gold_flag", "id", "VARCHAR", 1, 0.0),
        _col("gold_orders", "id", "VARCHAR", 1, 0.0),
    ]
    row_counts = {"gold_flag": 1, "gold_orders": 1}
    assert discover_relationship_candidates(columns, row_counts) == []


def test_unprofiled_columns_produce_no_candidates():
    columns = [
        _col("gold_users", "user_id", "VARCHAR", None, None),
        _col("gold_orders", "user_id", "VARCHAR", None, None),
    ]
    row_counts = {"gold_users": 10, "gold_orders": 25}
    assert discover_relationship_candidates(columns, row_counts) == []


def test_validate_containment_confirms_full_containment():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA pggold")
    con.execute(
        "CREATE TABLE pggold.gold_customers AS "
        "SELECT * FROM (VALUES ('c1'),('c2'),('c3')) v(customer_id)"
    )
    con.execute(
        "CREATE TABLE pggold.gold_invoices AS "
        "SELECT * FROM (VALUES ('c1'),('c1'),('c2')) v(customer_id)"
    )
    eng = _engine_over(con)
    out = eng.validate_containment("invoices", "customer_id", "customers", "customer_id")
    assert out["contained"] is True
    assert out["orphan_values"] == 0
    assert out["child_distinct"] == 2
    assert out["coverage"] == 1.0
    con.close()


def test_validate_containment_flags_orphans():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA pggold")
    con.execute(
        "CREATE TABLE pggold.gold_customers AS "
        "SELECT * FROM (VALUES ('c1'),('c2')) v(customer_id)"
    )
    con.execute(
        "CREATE TABLE pggold.gold_invoices AS "
        "SELECT * FROM (VALUES ('c1'),('c2'),('c9')) v(customer_id)"
    )
    eng = _engine_over(con)
    out = eng.validate_containment("invoices", "customer_id", "customers", "customer_id")
    assert out["contained"] is False
    assert out["orphan_values"] == 1
    assert out["child_distinct"] == 3
    assert out["coverage"] == round(2 / 3, 4)
    con.close()


def test_validate_containment_ignores_nulls_on_both_sides():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA pggold")
    con.execute(
        "CREATE TABLE pggold.gold_customers AS "
        "SELECT * FROM (VALUES ('c1'),(NULL)) v(customer_id)"
    )
    con.execute(
        "CREATE TABLE pggold.gold_invoices AS "
        "SELECT * FROM (VALUES ('c1'),(NULL),(NULL)) v(customer_id)"
    )
    eng = _engine_over(con)
    out = eng.validate_containment("invoices", "customer_id", "customers", "customer_id")
    assert out["contained"] is True
    assert out["child_distinct"] == 1
    assert out["orphan_values"] == 0
    con.close()


def test_validate_containment_rejects_unsafe_identifier():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA pggold")
    con.execute("CREATE TABLE pggold.gold_a AS SELECT 1 AS x")
    eng = _engine_over(con)
    with pytest.raises(Exception):
        eng.validate_containment("a", 'x"; DROP TABLE pggold.gold_a; --', "a", "x")
    con.close()
