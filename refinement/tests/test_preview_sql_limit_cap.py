"""Sprint v1.7 FIX 3 — preview_sql enforces a hard cap on `limit`.

A caller (LLM-generated SQL, malicious app) could pass limit=999_999_999
and pin the server with a runaway query. preview_sql now coerces:

    limit > _MAX_PREVIEW_LIMIT  → _MAX_PREVIEW_LIMIT (10_000)
    limit ≤ 0 or None           → _DEFAULT_PREVIEW_LIMIT (20)
    otherwise                   → unchanged

We verify the coercion by inspecting the LIMIT clause in the final SQL
that gets handed to DuckDB's execute().
"""
import re
import sys
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("duckdb", MagicMock())
sys.modules.setdefault("psycopg2", MagicMock())
sys.modules.setdefault("psycopg2.extras", MagicMock())

import os
os.environ.setdefault("MINIO_SECRET_KEY", "test")
os.environ.setdefault("MINIO_ACCESS_KEY", "test")
os.environ.setdefault("MINIO_ENDPOINT", "test")

from refinement.app.duckdb_engine import DuckDBEngine


@pytest.fixture
def engine():
    e = DuckDBEngine()
    mock_conn = MagicMock()
    # DESCRIBE used by RLS lookup returns a column the engine knows.
    describe_cursor = MagicMock()
    describe_cursor.fetchall.return_value = [("workspace_id", "varchar")]
    # The final SELECT returns nothing — we only care about the SQL.
    select_cursor = MagicMock()
    select_cursor.description = [("col", "VARCHAR")]
    select_cursor.fetchall.return_value = []
    detach_cursor = MagicMock()
    attach_cursor = MagicMock()
    mock_conn.execute.side_effect = [
        describe_cursor,
        detach_cursor,
        attach_cursor,
        select_cursor,
    ]
    e._conn = MagicMock(return_value=mock_conn)
    yield e, mock_conn


def _final_limit_from(mock_conn):
    """Return the LIMIT N value of the SQL that hit DuckDB.execute()."""
    final_sql = next(
        call[0][0]
        for call in reversed(mock_conn.execute.call_args_list)
        if isinstance(call[0][0], str) and re.search(r"\bLIMIT\s+\d+\b", call[0][0])
    )
    m = re.search(r"LIMIT\s+(\d+)", final_sql)
    assert m, f"no LIMIT clause in final SQL: {final_sql!r}"
    return int(m.group(1))


def test_limit_above_cap_is_clamped(engine):
    e, mock_conn = engine
    e.preview_sql(
        "SELECT * FROM pggold.gold_sales",
        limit=999_999,
        user_context={"workspace_id": "ws-1"},
    )
    assert _final_limit_from(mock_conn) == e._MAX_PREVIEW_LIMIT == 10_000


def test_limit_at_cap_is_preserved(engine):
    e, mock_conn = engine
    e.preview_sql(
        "SELECT * FROM pggold.gold_sales",
        limit=e._MAX_PREVIEW_LIMIT,
        user_context={"workspace_id": "ws-1"},
    )
    assert _final_limit_from(mock_conn) == 10_000


def test_reasonable_limit_passes_through(engine):
    e, mock_conn = engine
    e.preview_sql(
        "SELECT * FROM pggold.gold_sales",
        limit=20,
        user_context={"workspace_id": "ws-1"},
    )
    assert _final_limit_from(mock_conn) == 20


def test_zero_limit_falls_back_to_default(engine):
    e, mock_conn = engine
    e.preview_sql(
        "SELECT * FROM pggold.gold_sales",
        limit=0,
        user_context={"workspace_id": "ws-1"},
    )
    assert _final_limit_from(mock_conn) == e._DEFAULT_PREVIEW_LIMIT == 20


def test_negative_limit_falls_back_to_default(engine):
    e, mock_conn = engine
    e.preview_sql(
        "SELECT * FROM pggold.gold_sales",
        limit=-5,
        user_context={"workspace_id": "ws-1"},
    )
    assert _final_limit_from(mock_conn) == 20


def test_none_limit_falls_back_to_default(engine):
    e, mock_conn = engine
    e.preview_sql(
        "SELECT * FROM pggold.gold_sales",
        limit=None,  # type: ignore[arg-type]
        user_context={"workspace_id": "ws-1"},
    )
    assert _final_limit_from(mock_conn) == 20


def test_constant_tampering_guard():
    # Tampering safeguard: the cap shouldn't silently rise to absurd values.
    from refinement.app.duckdb_engine import DuckDBEngine as _DDE
    assert _DDE._MAX_PREVIEW_LIMIT <= 100_000, \
        "preview cap raised above 100k — re-check intent before merging"
    assert _DDE._DEFAULT_PREVIEW_LIMIT >= 1
