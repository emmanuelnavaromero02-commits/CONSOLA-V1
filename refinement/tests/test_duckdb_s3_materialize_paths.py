from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

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


def test_copy_to_parquet_writes_local_temp_and_uploads(monkeypatch):
    engine = DuckDBEngine()
    uploaded: list[tuple[str, str]] = []
    unlinked: list[str] = []
    con = MagicMock()

    class FakeTmp:
        name = "/tmp/omega-materialize-test.parquet"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        "refinement.app.duckdb_engine.tempfile.NamedTemporaryFile",
        lambda **kwargs: FakeTmp(),
    )
    monkeypatch.setattr(
        engine,
        "_upload_local_parquet",
        lambda local, target: uploaded.append((local, target)) or target,
    )
    monkeypatch.setattr("refinement.app.duckdb_engine.os.unlink", lambda path: unlinked.append(path))

    engine._copy_to_parquet(con, "SELECT 1 AS ok", "s3://lakehouse/silver/x/data.parquet")

    assert uploaded == [
        ("/tmp/omega-materialize-test.parquet", "s3://lakehouse/silver/x/data.parquet")
    ]
    assert unlinked == ["/tmp/omega-materialize-test.parquet"]
    con.execute.assert_called_once_with(
        "COPY (SELECT 1 AS ok) TO '/tmp/omega-materialize-test.parquet' "
        "(FORMAT PARQUET, OVERWRITE_OR_IGNORE true)"
    )


def test_upload_local_parquet_uses_retry_key_without_overwriting(monkeypatch):
    uploaded: list[tuple[str, str, str]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.calls = 0

        def fput_object(self, bucket, key, local_path, content_type):
            self.calls += 1
            assert bucket == "lakehouse"
            if self.calls == 1:
                raise RuntimeError("transient minio write error")
            uploaded.append((key, local_path, content_type))

    monkeypatch.setitem(sys.modules, "minio", SimpleNamespace(Minio=FakeClient))
    engine = DuckDBEngine()

    result = engine._upload_local_parquet(
        "/tmp/materialized.parquet",
        "s3://lakehouse/silver/hubspot/hubspot_deals_latest/data.parquet",
    )

    assert len(uploaded) == 1
    key, local_path, content_type = uploaded[0]
    assert key.startswith("silver/hubspot/hubspot_deals_latest/data.retry2-")
    assert key.endswith(".parquet")
    assert local_path == "/tmp/materialized.parquet"
    assert content_type == "application/octet-stream"
    assert result == f"s3://lakehouse/{key}"


def test_snapshot_path_is_scoped_and_unique():
    engine = DuckDBEngine()
    ctx = {"tenant_id": "tenant-1", "workspace_id": "workspace-1"}

    first = engine._snapshot_path("silver", "hubspot", "hubspot_deals_latest", ctx)
    second = engine._snapshot_path("silver", "hubspot", "hubspot_deals_latest", ctx)

    assert first.startswith(
        "s3://lakehouse/silver/hubspot/hubspot_deals_latest/"
        "tenant_id=tenant-1/workspace_id=workspace-1/_snapshots/"
    )
    assert first.endswith(".parquet")
    assert first != second


def test_scope_storage_sql_rewrites_registered_silver_to_latest_snapshot(monkeypatch):
    engine = DuckDBEngine()
    latest = (
        "s3://lakehouse/silver/hubspot/hubspot_deals_latest/"
        "tenant_id=tenant-1/workspace_id=workspace-1/_snapshots/20260531.parquet"
    )
    monkeypatch.setattr(engine, "_latest_materialized_uri", lambda *args, **kwargs: latest)
    sql = "SELECT * FROM read_parquet('s3://lakehouse/silver/hubspot/hubspot_deals_latest/data.parquet')"

    rewritten = engine._scope_storage_sql(
        sql,
        ["silver/hubspot/hubspot_deals_latest"],
        {"tenant_id": "tenant-1", "workspace_id": "workspace-1"},
    )

    assert latest in rewritten
    assert "hubspot_deals_latest/data.parquet" not in rewritten


@pytest.mark.parametrize("glob", ["*.parquet", "**/*.parquet"])
def test_scope_storage_sql_rewrites_registered_silver_globs_to_latest_snapshot(monkeypatch, glob):
    engine = DuckDBEngine()
    latest = (
        "s3://lakehouse/silver/hubspot/hubspot_deals_latest/"
        "tenant_id=tenant-1/workspace_id=workspace-1/_snapshots/20260531.parquet"
    )
    monkeypatch.setattr(engine, "_latest_materialized_uri", lambda *args, **kwargs: latest)
    sql = f"SELECT * FROM read_parquet('s3://lakehouse/silver/hubspot/hubspot_deals_latest/{glob}')"

    rewritten = engine._scope_storage_sql(
        sql,
        ["silver/hubspot/hubspot_deals_latest"],
        {"tenant_id": "tenant-1", "workspace_id": "workspace-1"},
    )

    assert latest in rewritten
    assert f"hubspot_deals_latest/{glob}" not in rewritten


def test_validate_scoped_storage_sql_rejects_cross_tenant_paths():
    engine = DuckDBEngine()

    with pytest.raises(ValueError, match="outside the caller tenant/workspace scope"):
        engine._validate_scoped_storage_sql(
            "SELECT * FROM read_parquet('s3://lakehouse/silver/hubspot/x/tenant_id=tenant-b/workspace_id=workspace-b/data.parquet')",
            {"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
        )


def test_validate_scoped_storage_sql_rejects_unapproved_bucket():
    engine = DuckDBEngine()

    with pytest.raises(ValueError, match="unapproved bucket"):
        engine._validate_scoped_storage_sql(
            "SELECT * FROM read_parquet('s3://other-bucket/silver/hubspot/x/tenant_id=tenant-a/workspace_id=workspace-a/data.parquet')",
            {"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
        )


def test_latest_materialized_uri_uses_parameterized_s3_like(monkeypatch):
    captured: dict[str, object] = {}

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params

        def fetchone(self):
            return ("s3://lakehouse/silver/hubspot/x/_snapshots/latest.parquet",)

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def close(self):
            pass

    engine = DuckDBEngine()
    monkeypatch.setattr(engine, "_pg_conn", lambda: FakeConn())

    uri = engine._latest_materialized_uri("silver", "hubspot", "x")

    assert uri == "s3://lakehouse/silver/hubspot/x/_snapshots/latest.parquet"
    assert "storage_uri LIKE %s" in str(captured["query"])
    assert "'s3://%'" not in str(captured["query"])
    assert captured["params"] == ["x", "hubspot", "silver", "s3://%"]
