from __future__ import annotations

from refinement.app.duckdb_engine import DuckDBEngine


def test_bronze_source_from_entity_first_scoped_key():
    engine = DuckDBEngine()
    ctx = {"tenant_id": "tenant-a", "workspace_id": "workspace-a"}

    source = engine._bronze_source_from_object_key(
        (
            "raw/sap_successfactors/Candidate/"
            "tenant_id=tenant-a/workspace_id=workspace-a/"
            "load_date=2026-06-25/batch_id=run-1/data.parquet"
        ),
        ctx,
    )

    assert source == "raw/sap_successfactors/Candidate"


def test_bronze_source_discovery_rejects_foreign_workspace():
    engine = DuckDBEngine()
    ctx = {"tenant_id": "tenant-a", "workspace_id": "workspace-a"}

    source = engine._bronze_source_from_object_key(
        (
            "raw/sap_successfactors/Candidate/"
            "tenant_id=tenant-b/workspace_id=workspace-b/"
            "load_date=2026-06-25/batch_id=run-1/data.parquet"
        ),
        ctx,
    )

    assert source is None


def test_list_sources_uses_object_store_and_scope(monkeypatch):
    engine = DuckDBEngine()

    class FakePaginator:
        def paginate(self, *, Bucket, Prefix):
            assert Bucket == engine.minio_bucket
            assert Prefix == "raw/sap_successfactors/"
            return [
                {
                    "Contents": [
                        {
                            "Key": (
                                "raw/sap_successfactors/Candidate/"
                                "tenant_id=tenant-a/workspace_id=workspace-a/"
                                "load_date=2026-06-25/batch_id=run-1/data.parquet"
                            )
                        },
                        {
                            "Key": (
                                "raw/sap_successfactors/User/"
                                "tenant_id=tenant-a/workspace_id=workspace-a/"
                                "load_date=2026-06-25/batch_id=run-1/data.parquet"
                            )
                        },
                        {
                            "Key": (
                                "raw/sap_successfactors/Candidate/"
                                "tenant_id=tenant-b/workspace_id=workspace-b/"
                                "load_date=2026-06-25/batch_id=run-1/data.parquet"
                            )
                        },
                    ]
                }
            ]

    class FakeS3Client:
        def get_paginator(self, name):
            assert name == "list_objects_v2"
            return FakePaginator()

    monkeypatch.setattr(engine, "_uses_aws_s3_credential_chain", lambda: True)
    monkeypatch.setattr(engine, "_boto3_s3_client", lambda: FakeS3Client())

    sources = engine.list_sources(
        {"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
        ["raw/sap_successfactors/"],
    )

    assert sources == [
        "raw/sap_successfactors/Candidate",
        "raw/sap_successfactors/User",
    ]
