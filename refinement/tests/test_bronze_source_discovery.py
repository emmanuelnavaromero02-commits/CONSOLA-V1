from __future__ import annotations

from types import SimpleNamespace

from refinement.app.duckdb_engine import DuckDBEngine
from omega_lakehouse import ObjectStat


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

    class FakeStorage:
        def iter_list(self, prefix):
            assert prefix == "raw/sap_successfactors/"
            keys = [
                (
                    "raw/sap_successfactors/Candidate/"
                    "tenant_id=tenant-a/workspace_id=workspace-a/"
                    "load_date=2026-06-25/batch_id=run-1/data.parquet"
                ),
                (
                    "raw/sap_successfactors/User/"
                    "tenant_id=tenant-a/workspace_id=workspace-a/"
                    "load_date=2026-06-25/batch_id=run-1/data.parquet"
                ),
                (
                    "raw/sap_successfactors/Candidate/"
                    "tenant_id=tenant-b/workspace_id=workspace-b/"
                    "load_date=2026-06-25/batch_id=run-1/data.parquet"
                ),
            ]
            for key in keys:
                yield ObjectStat(key=key, uri=f"s3://lakehouse/{key}", size=1)

    monkeypatch.setattr(engine, "storage", FakeStorage())

    sources = engine.list_sources(
        {"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
        ["raw/sap_successfactors/"],
    )

    assert sources == [
        "raw/sap_successfactors/Candidate",
        "raw/sap_successfactors/User",
    ]


def test_preview_source_keeps_schema_when_sample_rows_fail(monkeypatch):
    engine = DuckDBEngine()

    class FakeConnection:
        def execute(self, sql):
            if sql.startswith("DESCRIBE"):
                return SimpleNamespace(
                    fetchall=lambda: [
                        ("userId", "VARCHAR"),
                        ("status", "VARCHAR"),
                    ]
                )
            if sql.startswith("SELECT *"):
                raise RuntimeError("sample read failed")
            raise AssertionError(sql)

    monkeypatch.setattr(engine, "_conn", lambda: FakeConnection())

    payload = engine.preview_source(
        "raw/sap_successfactors/User",
        user_context={"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
    )

    assert payload["status"] == "partial"
    assert [column["name"] for column in payload["schema"]] == ["userId", "status"]
    assert payload["data"] == []
    assert payload["warnings"][0]["reason"] == "sample_read_error"
