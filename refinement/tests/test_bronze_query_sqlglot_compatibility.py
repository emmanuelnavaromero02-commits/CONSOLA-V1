"""Compatibility contract for SQLGlot's typed DuckDB function nodes."""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from app.sql_table_function_policy import StorageRead, validate_table_function_query


def test_typed_read_parquet_node_keeps_closed_allowlist_compatibility():
    sql = (
        "SELECT * FROM read_parquet("
        "'s3://lakehouse/raw/replicon/pnl_mensual/**/*.parquet')"
    )
    tree = sqlglot.parse_one(sql, read="duckdb")
    relation_function = next(tree.find_all(exp.Func))

    if hasattr(exp, "ReadParquet"):
        assert type(relation_function).__name__ == "ReadParquet"
        assert relation_function.sql_name() == "READ_PARQUET"
    else:
        assert isinstance(relation_function, exp.Anonymous)

    assert validate_table_function_query(sql) == (
        StorageRead("s3://lakehouse/raw/replicon/pnl_mensual/**/*.parquet"),
    )
