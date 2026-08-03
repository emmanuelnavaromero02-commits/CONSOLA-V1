from __future__ import annotations

import pytest

from refinement.app.duckdb_engine import DuckDBEngine
from refinement.app.sql_table_function_policy import (
    TableFunctionPolicyError,
    validate_table_function_query,
)


@pytest.mark.parametrize(
    "sql",
    [
        'WITH "Ä.csv" AS (SELECT 1) SELECT * FROM "ä.csv"',
        'WITH "Σ.csv" AS (SELECT 1) SELECT * FROM "σ.csv"',
    ],
)
def test_unicode_casefold_does_not_launder_local_relation(sql: str) -> None:
    with pytest.raises(TableFunctionPolicyError, match="safety policy"):
        validate_table_function_query(sql)

    engine = object.__new__(DuckDBEngine)
    with pytest.raises(ValueError, match="safety policy"):
        engine._validate_safe_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        'WITH x AS (SELECT 1) SELECT * FROM "/secret.csv" AS x',
        'WITH "x" AS (SELECT 1) SELECT * FROM "/secret.csv" AS "x"',
    ],
)
def test_relation_alias_does_not_launder_local_relation(sql: str) -> None:
    with pytest.raises(TableFunctionPolicyError, match="safety policy"):
        validate_table_function_query(sql)

    engine = object.__new__(DuckDBEngine)
    with pytest.raises(ValueError, match="safety policy"):
        engine._validate_safe_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        'SELECT * FROM foo."bar.csv"',
        'SELECT * FROM main."secret.csv"',
        'SELECT * FROM pggold."gold_secret.csv"',
        'SELECT * FROM "foo"."bar"."csv"',
        'WITH "bar.csv" AS (SELECT 1) SELECT * FROM foo."bar.csv"',
        'WITH "BAR.CSV" AS (SELECT 1) SELECT * FROM foo."bar.csv"',
        'WITH "csv" AS (SELECT 1) SELECT * FROM "foo"."bar"."csv"',
    ],
)
def test_qualified_relation_does_not_launder_local_relation(sql: str) -> None:
    with pytest.raises(TableFunctionPolicyError, match="safety policy"):
        validate_table_function_query(sql)

    engine = object.__new__(DuckDBEngine)
    with pytest.raises(ValueError, match="safety policy"):
        engine._validate_safe_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        'WITH first AS (SELECT * FROM "later"), later AS (SELECT 1) '
        "SELECT * FROM first",
        'WITH "First" AS (SELECT * FROM "LATER"), "later" AS (SELECT 1) '
        'SELECT * FROM "FIRST"',
        'SELECT * FROM pggold."gold_sales"',
    ],
)
def test_safe_forward_ctes_and_registered_gold_shape_remain_allowed(sql: str) -> None:
    assert validate_table_function_query(sql) == ()
    engine = object.__new__(DuckDBEngine)
    engine._validate_safe_sql(sql)


def test_publication_relation_requires_server_resolved_provenance() -> None:
    sql = (
        "SELECT * FROM pggold.omega_publication_gold."
        '"run_0123456789abcdef0123456789abcdef"'
    )
    with pytest.raises(TableFunctionPolicyError, match="safety policy"):
        validate_table_function_query(sql)
    assert (
        validate_table_function_query(
            sql,
            allow_server_resolved_publication_relation=True,
        )
        == ()
    )

    engine = object.__new__(DuckDBEngine)
    with pytest.raises(ValueError, match="safety policy"):
        engine._validate_safe_sql(sql)
    engine._validate_safe_sql(
        sql,
        allow_server_resolved_publication_relation=True,
    )
