from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from refinement.app.duckdb_engine import DuckDBEngine


RAW_GOLD_SQL = 'SELECT * FROM pggold."gold_sales"'
PUBLISHED_GOLD_SQL = (
    "SELECT * FROM pggold.omega_publication_gold."
    '"run_0123456789abcdef0123456789abcdef"'
)


def _preview_engine() -> tuple[DuckDBEngine, MagicMock]:
    engine = object.__new__(DuckDBEngine)
    engine._duckdb_lock = threading.RLock()
    connection = MagicMock()
    cursor = connection.execute.return_value
    cursor.description = [("value", "INTEGER")]
    cursor.fetchall.return_value = [(1,)]
    engine.get_rls_filters = lambda *_args: (PUBLISHED_GOLD_SQL, [])
    engine._conn = lambda: connection
    engine._inject_bucket = lambda sql: sql
    engine._scope_storage_sql = lambda sql, *_args: sql
    engine._inject_latest_date = lambda sql, *_args: sql
    engine._validate_scoped_storage_sql = lambda *_args: None
    engine._pg_gold_attach = lambda *_args: None
    return engine, connection


def test_registered_gold_binding_is_revalidated_after_server_rewrite() -> None:
    engine, connection = _preview_engine()

    result = engine.preview_sql(RAW_GOLD_SQL, limit=20)

    assert result == {
        "schema": [{"name": "value", "type": "INTEGER"}],
        "data": [{"value": 1}],
        "row_count": 1,
    }
    connection.execute.assert_called_once()


def test_raw_published_relation_is_denied_before_server_rewrite() -> None:
    engine, connection = _preview_engine()

    with pytest.raises(ValueError, match="safety policy"):
        engine.preview_sql(PUBLISHED_GOLD_SQL, limit=20)
    connection.execute.assert_not_called()
