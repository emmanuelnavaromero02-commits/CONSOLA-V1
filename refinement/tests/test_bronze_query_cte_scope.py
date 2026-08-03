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
