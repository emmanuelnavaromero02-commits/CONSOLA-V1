from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from omega_lakehouse import ObjectAlreadyExists
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
    removed: list[tuple[str, bool]] = []

    engine = DuckDBEngine()
    engine.storage = SimpleNamespace(
        delete_prefix=lambda prefix, *, require_trailing_slash: removed.append((prefix, require_trailing_slash))
    )

    engine._delete_s3_prefix("s3://lakehouse/silver/hubspot/hubspot_deals_latest/data.parquet")

    assert removed == [("silver/hubspot/hubspot_deals_latest/data.parquet", False)]


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


def test_upload_local_parquet_uses_retry_key_without_overwriting(tmp_path):
    uploaded: list[tuple[str, bytes, bool]] = []

    class FakeStorage:
        def __init__(self):
            self.calls = 0

        def put_file(self, key, local_path, *, overwrite):
            self.calls += 1
            if self.calls == 1:
                raise ObjectAlreadyExists("exists")
            uploaded.append((key, local_path.read_bytes(), overwrite))
            return SimpleNamespace(uri=f"s3://lakehouse/{key}")

    engine = DuckDBEngine()
    engine.storage = FakeStorage()
    local = tmp_path / "materialized.parquet"
    local.write_bytes(b"parquet")

    result = engine._upload_local_parquet(
        str(local),
        "s3://lakehouse/silver/hubspot/hubspot_deals_latest/data.parquet",
    )

    assert len(uploaded) == 1
    key, payload, overwrite = uploaded[0]
    assert key.startswith("silver/hubspot/hubspot_deals_latest/data.retry2-")
    assert key.endswith(".parquet")
    assert payload == b"parquet"
    assert overwrite is False
    assert result == f"s3://lakehouse/{key}"


def test_aws_s3_without_static_keys_uses_credential_chain(monkeypatch):
    statements: list[str] = []

    class FakeConn:
        def execute(self, sql):
            statements.append(sql)
            return self

    monkeypatch.setenv("MINIO_ENDPOINT", "s3.us-east-1.amazonaws.com")
    monkeypatch.setenv("MINIO_BUCKET", "modecissions-lakehouse-test")
    monkeypatch.delenv("MINIO_ACCESS_KEY", raising=False)
    monkeypatch.delenv("MINIO_SECRET_KEY", raising=False)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setattr("refinement.app.duckdb_engine.duckdb.connect", lambda: FakeConn())

    engine = DuckDBEngine()
    engine._conn()

    combined = "\n".join(statements)
    assert "PROVIDER credential_chain" in combined
    assert "s3_url_style='vhost'" in combined
    assert "s3_access_key_id=''" not in combined
    assert "s3_secret_access_key=''" not in combined


def test_gcs_lakehouse_configures_duckdb_gcs_secret(monkeypatch):
    statements: list[str] = []

    class FakeConn:
        def execute(self, sql):
            statements.append(sql)
            return self

    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs")
    monkeypatch.setenv("GCS_BUCKET", "modecissions-gcs-lakehouse")
    monkeypatch.setenv("GCS_ACCESS_KEY_ID", "gcs-key")
    monkeypatch.setenv("GCS_SECRET_ACCESS_KEY", "gcs-secret")
    monkeypatch.setattr("refinement.app.duckdb_engine.duckdb.connect", lambda: FakeConn())

    engine = DuckDBEngine()
    engine._conn()

    combined = "\n".join(statements)
    assert engine._storage_uri("raw/x.parquet") == "gs://modecissions-gcs-lakehouse/raw/x.parquet"
    assert "TYPE gcs" in combined
    assert "KEY_ID 'gcs-key'" in combined
    assert "SECRET " in combined
    assert "s3_endpoint" not in combined
    assert "gcs_fuse" not in combined


def test_gcs_lakehouse_sanitizes_duckdb_secret_setup_errors(monkeypatch):
    class FakeConn:
        def execute(self, sql):
            if "CREATE OR REPLACE SECRET omega_gcs" in sql:
                raise RuntimeError("bad sql contained gcs-secret")
            return self

    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs")
    monkeypatch.setenv("GCS_BUCKET", "modecissions-gcs-lakehouse")
    monkeypatch.setenv("GCS_ACCESS_KEY_ID", "gcs-key")
    monkeypatch.setenv("GCS_SECRET_ACCESS_KEY", "gcs-secret")
    monkeypatch.setattr("refinement.app.duckdb_engine.duckdb.connect", lambda: FakeConn())

    engine = DuckDBEngine()

    with pytest.raises(ValueError) as exc:
        engine._conn()
    assert str(exc.value) == "GCS lakehouse DuckDB credential setup failed"
    assert "gcs-secret" not in str(exc.value)


def test_gcs_lakehouse_requires_hmac_for_duckdb_reads(monkeypatch):
    class FakeConn:
        def execute(self, _sql):
            return self

    monkeypatch.setenv("LAKEHOUSE_PROVIDER", "gcs")
    monkeypatch.setenv("GCS_BUCKET", "modecissions-gcs-lakehouse")
    monkeypatch.delenv("GCS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("GCS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("LAKEHOUSE_ACCESS_KEY", raising=False)
    monkeypatch.delenv("LAKEHOUSE_SECRET_KEY", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "aws-key-must-not-be-used")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret-must-not-be-used")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "minio-key-must-not-be-used")
    monkeypatch.setenv("MINIO_SECRET_KEY", "minio-secret-must-not-be-used")
    monkeypatch.setattr("refinement.app.duckdb_engine.duckdb.connect", lambda: FakeConn())

    engine = DuckDBEngine()

    with pytest.raises(ValueError, match="GCS lakehouse refinement reads require HMAC credentials"):
        engine._conn()


def test_minio_uses_path_style(monkeypatch):
    statements: list[str] = []

    class FakeConn:
        def execute(self, sql):
            statements.append(sql)
            return self

    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "minio")
    monkeypatch.setenv("MINIO_SECRET_KEY", "secret")
    monkeypatch.setattr("refinement.app.duckdb_engine.duckdb.connect", lambda: FakeConn())

    engine = DuckDBEngine()
    engine._conn()

    combined = "\n".join(statements)
    assert "s3_url_style='path'" in combined


def test_missing_materialized_dependencies_reports_unready_silver_sources(monkeypatch):
    engine = DuckDBEngine()
    monkeypatch.setattr(
        engine,
        "_latest_materialized_uri",
        lambda _layer, _cartridge, name, _ctx: "s3://lakehouse/ok.parquet" if name == "ready" else None,
    )

    missing = engine.missing_materialized_dependencies(
        [
            "raw/sap_successfactors/EmpCompensation",
            "silver/sap_successfactors/ready",
            "silver/sap_successfactors/missing",
            "gold/sap_successfactors/missing_gold",
        ],
        {"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
    )

    assert missing == [
        "silver/sap_successfactors/missing",
        "gold/sap_successfactors/missing_gold",
    ]


def test_aws_s3_upload_uses_boto3_credential_chain(monkeypatch):
    uploads: list[tuple[str, str, bool]] = []

    class FakeStorage:
        def put_file(self, key, local_path, *, overwrite):
            uploads.append((str(local_path), key, overwrite))
            return SimpleNamespace(uri=f"s3://modecissions-lakehouse-test/{key}")

    monkeypatch.setenv("MINIO_ENDPOINT", "s3.us-east-1.amazonaws.com")
    monkeypatch.setenv("MINIO_BUCKET", "modecissions-lakehouse-test")
    monkeypatch.delenv("MINIO_ACCESS_KEY", raising=False)
    monkeypatch.delenv("MINIO_SECRET_KEY", raising=False)

    engine = DuckDBEngine()
    engine.storage = FakeStorage()

    result = engine._upload_local_parquet(
        __file__,
        "s3://modecissions-lakehouse-test/silver/hubspot/hubspot_deals_latest/data.parquet",
    )

    assert uploads == [
        (
            __file__,
            "silver/hubspot/hubspot_deals_latest/data.parquet",
            False,
        )
    ]
    assert result == "s3://modecissions-lakehouse-test/silver/hubspot/hubspot_deals_latest/data.parquet"


def test_aws_s3_delete_prefix_uses_boto3_credential_chain(monkeypatch):
    deleted: list[tuple[str, bool]] = []

    class FakeStorage:
        def delete_prefix(self, prefix, *, require_trailing_slash):
            deleted.append((prefix, require_trailing_slash))

    monkeypatch.setenv("MINIO_ENDPOINT", "s3.us-east-1.amazonaws.com")
    monkeypatch.setenv("MINIO_BUCKET", "modecissions-lakehouse-test")
    monkeypatch.delenv("MINIO_ACCESS_KEY", raising=False)
    monkeypatch.delenv("MINIO_SECRET_KEY", raising=False)

    engine = DuckDBEngine()
    engine.storage = FakeStorage()

    engine._delete_s3_prefix("s3://modecissions-lakehouse-test/silver/hubspot/hubspot_deals_latest/data.parquet")

    assert deleted == [("silver/hubspot/hubspot_deals_latest/data.parquet", False)]


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
