from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest


@pytest.fixture
def engine():
    repo = Path(__file__).resolve().parents[1]
    sys.path[:] = [
        p
        for p in sys.path
        if not any(
            s in p
            for s in ("/cartridges/", "/console", "/vault", "/workspace", "/mcp-infra")
        )
    ]
    sys.path.insert(0, str(repo))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from refinement.app.duckdb_engine import DuckDBEngine

    return DuckDBEngine()


def _seed(con):
    con.execute(
        """
        CREATE TABLE t AS SELECT * FROM (VALUES
            (1, 'a', 10.0),
            (2, 'a', 20.0),
            (3, NULL, 30.0),
            (4, 'b', NULL)
        ) v(id, cat, amt)
        """
    )


def test_profile_columns_null_rate_distinct_min_max(engine):
    con = duckdb.connect()
    try:
        _seed(con)
        stats = engine._profile_columns(con, "t")

        assert stats["id"]["null_rate"] == 0.0
        assert stats["id"]["distinct_count"] == 4
        assert stats["id"]["min"] == "1"
        assert stats["id"]["max"] == "4"

        assert stats["cat"]["null_rate"] == 0.25
        assert stats["cat"]["distinct_count"] == 2
        assert stats["cat"]["min"] == "a"
        assert stats["cat"]["max"] == "b"

        assert stats["amt"]["null_rate"] == 0.25
        assert stats["amt"]["distinct_count"] == 3
    finally:
        con.close()


def test_profile_columns_over_parquet_silver_path(engine, tmp_path):
    con = duckdb.connect()
    try:
        con.execute(
            "CREATE TABLE t AS SELECT * FROM (VALUES "
            "(1,'x'),(2,'x'),(3,NULL),(4,'x')) v(id, cat)"
        )
        parquet = tmp_path / "d.parquet"
        con.execute(f"COPY t TO '{str(parquet)}' (FORMAT PARQUET)")

        stats = engine._profile_columns(con, f"read_parquet('{str(parquet)}')")
        assert stats["cat"]["null_rate"] == 0.25
        assert stats["cat"]["distinct_count"] == 1
        assert stats["id"]["null_rate"] == 0.0
        assert stats["id"]["distinct_count"] == 4
    finally:
        con.close()


def test_profile_columns_bad_relation_never_raises(engine):
    con = duckdb.connect()
    try:
        assert engine._profile_columns(con, "nonexistent_relation_xyz") == {}
    finally:
        con.close()


def test_profile_columns_all_null_column(engine):
    con = duckdb.connect()
    try:
        con.execute(
            "CREATE TABLE t AS SELECT * FROM (VALUES "
            "(1, NULL), (2, NULL), (3, NULL), (4, NULL)) v(id, empty_col)"
        )
        stats = engine._profile_columns(con, "t")
        assert stats["empty_col"]["null_rate"] == 1.0
        assert "empty_col" in stats
    finally:
        con.close()
