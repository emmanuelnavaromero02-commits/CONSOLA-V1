from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from refinement.app.duckdb_engine import DuckDBEngine


def test_unscoped_bronze_path_stays_on_legacy_partition_layout():
    engine = DuckDBEngine()

    assert (
        engine._bronze_path("raw/hubspot/deals")
        == "s3://lakehouse/raw/hubspot/deals/load_date=*/batch_id=*/*.parquet"
    )


def test_scope_storage_sql_rewrites_unscoped_raw_glob_to_legacy_only():
    engine = DuckDBEngine()
    sql = "SELECT * FROM read_parquet('s3://lakehouse/raw/hubspot/deals/**/*.parquet')"

    rewritten = engine._scope_storage_sql(sql, ["raw/hubspot/deals"], None)

    assert "raw/hubspot/deals/load_date=*/batch_id=*/*.parquet" in rewritten
    assert "raw/hubspot/deals/**/*.parquet" not in rewritten


def test_delete_s3_prefix_removes_existing_object_and_children(monkeypatch):
    removed: list[str] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def list_objects(self, bucket, prefix, recursive):
            assert bucket == "lakehouse"
            assert prefix == "silver/hubspot/hubspot_deals_latest/data.parquet"
            assert recursive is True
            return [
                SimpleNamespace(object_name=f"{prefix}/part.1"),
                SimpleNamespace(object_name=f"{prefix}/xl.meta"),
            ]

        def remove_object(self, bucket, key):
            assert bucket == "lakehouse"
            removed.append(key)

    monkeypatch.setitem(sys.modules, "minio", SimpleNamespace(Minio=FakeClient))
    engine = DuckDBEngine()

    engine._delete_s3_prefix("s3://lakehouse/silver/hubspot/hubspot_deals_latest/data.parquet")

    assert removed == [
        "silver/hubspot/hubspot_deals_latest/data.parquet/part.1",
        "silver/hubspot/hubspot_deals_latest/data.parquet/xl.meta",
        "silver/hubspot/hubspot_deals_latest/data.parquet",
    ]


def test_copy_to_parquet_deletes_before_copy(monkeypatch):
    engine = DuckDBEngine()
    deleted: list[str] = []
    con = MagicMock()

    monkeypatch.setattr(engine, "_delete_s3_prefix", lambda path: deleted.append(path))

    engine._copy_to_parquet(con, "SELECT 1 AS ok", "s3://lakehouse/silver/x/data.parquet")

    assert deleted == ["s3://lakehouse/silver/x/data.parquet"]
    con.execute.assert_called_once_with(
        "COPY (SELECT 1 AS ok) TO 's3://lakehouse/silver/x/data.parquet' "
        "(FORMAT PARQUET, OVERWRITE_OR_IGNORE true)"
    )
