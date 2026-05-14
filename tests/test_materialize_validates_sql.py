"""Sprint v1.32 — materialize must use the same SQL safety gate as preview."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from refinement.app.duckdb_engine import DuckDBEngine


def test_materialize_blocks_local_file_reads_before_duckdb_execution():
    engine = DuckDBEngine()
    engine._conn = MagicMock(side_effect=AssertionError("_conn should not be opened for unsafe SQL"))

    with pytest.raises(ValueError, match="SQL blocked by safety policy|blocked local file path"):
        engine.materialize(
            {
                "name": "unsafe_dataset",
                "cartridge": "replicon",
                "layer": "silver",
                "sql_def": "SELECT * FROM read_csv('/etc/passwd')",
                "sources": [],
            }
        )


def test_materialize_blocks_ddl_before_duckdb_execution():
    engine = DuckDBEngine()
    engine._conn = MagicMock(side_effect=AssertionError("_conn should not be opened for unsafe SQL"))

    with pytest.raises(ValueError, match="SQL blocked by safety policy"):
        engine.materialize(
            {
                "name": "unsafe_dataset",
                "cartridge": "replicon",
                "layer": "gold",
                "sql_def": "DROP TABLE pggold.gold_sales",
                "sources": [],
            }
        )
