from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from refinement.app.duckdb_engine import DuckDBEngine
from refinement.app.sec_edgar_manifest import FACT_CONFIG, complete_manifests
from refinement.app.sec_edgar_materializer import (
    _gold_sql,
    _quality_sql,
    _read_parquet_sql,
    materialize_sec_dataset,
)


class FakeStorage:
    config = SimpleNamespace(provider="minio", bucket="lakehouse")

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def iter_list(self, prefix: str):
        return iter(SimpleNamespace(key=key) for key in sorted(self.objects) if key.startswith(prefix))

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def uri_for(self, key: str) -> str:
        return f"s3://lakehouse/{key}"


def _manifest(entity: str, status: str, *, batch: str = "batch-a") -> bytes:
    prefix = (
        f"raw/sec_edgar/{entity}/tenant_id=t/workspace_id=w/"
        f"load_date=2026-07-11/batch_id={batch}/"
    )
    return json.dumps(
        {
            "status": status,
            "cartridge_id": "sec_edgar",
            "entity": entity,
            "load_date": "2026-07-11",
            "run_id": batch,
            "final_prefix": prefix,
            "files": [{"name": f"{entity}.parquet", "key": f"{prefix}{entity}.parquet"}],
        }
    ).encode()


def test_complete_manifests_ignores_pending_corrupt_and_other_entities():
    storage = FakeStorage()
    base = "raw/sec_edgar/company_facts/tenant_id=t/workspace_id=w/load_date=2026-07-11"
    storage.objects[f"{base}/batch_id=complete/manifest.json"] = _manifest("company_facts", "complete", batch="complete")
    storage.objects[f"{base}/batch_id=pending/manifest.json"] = _manifest("company_facts", "pending", batch="pending")
    storage.objects[f"{base}/batch_id=bad/manifest.json"] = b"{not-json"
    storage.objects["raw/sec_edgar/company_metadata/tenant_id=t/workspace_id=w/load_date=2026-07-11/batch_id=x/manifest.json"] = _manifest(
        "company_metadata", "complete", batch="x"
    )

    manifests = complete_manifests(storage, "company_facts", {"tenant_id": "t", "workspace_id": "w"})

    assert [item.run_id for item in manifests] == ["complete"]
    assert manifests[0].parquet_uri.endswith("/company_facts.parquet")


def test_manifest_discovery_is_scoped_by_tenant_and_workspace():
    storage = FakeStorage()
    requested = "raw/sec_edgar/company_facts/tenant_id=t/workspace_id=w/load_date=2026-07-11"
    other = "raw/sec_edgar/company_facts/tenant_id=t/workspace_id=other/load_date=2026-07-11"
    storage.objects[f"{requested}/batch_id=ok/manifest.json"] = _manifest("company_facts", "complete", batch="ok")
    storage.objects[f"{other}/batch_id=leak/manifest.json"] = _manifest("company_facts", "complete", batch="leak")

    manifests = complete_manifests(storage, "company_facts", {"tenant_id": "t", "workspace_id": "w"})

    assert [item.run_id for item in manifests] == ["ok"]


def test_read_parquet_sql_uses_manifest_selected_uri_list_only():
    sql = _read_parquet_sql(["s3://lakehouse/raw/sec_edgar/x/file.parquet"])

    assert "read_parquet(['s3://lakehouse/raw/sec_edgar/x/file.parquet']" in sql
    assert "**/*.parquet" not in sql


def test_quality_sql_contains_exact_confidence_weights_and_statuses():
    sql = _quality_sql("s3://lakehouse/silver/facts.parquet", "s3://lakehouse/silver/meta.parquet")

    for token in ("0.30 * source_trust_score", "0.25 * schema_validity_score", "0.20 * freshness_score"):
        assert token in sql
    assert "0.15 * value_validity_score" in sql
    assert "0.10 * completeness_score" in sql
    assert "insufficient_data" in sql
    assert "stale" in sql
    assert "ready" in sql


def test_gold_sql_exposes_stale_rows_with_usable_flag():
    sql = _gold_sql("s3://lakehouse/silver/facts.parquet", "s3://lakehouse/silver/quality.parquet")

    assert "quality.status AS freshness_status" in sql
    assert "quality.confidence >= 0.80 AND quality.status = 'ready'" in sql
    assert "usable" in sql


def test_sec_freshness_slas_are_long_filing_windows():
    slas = {(item["cik"], item["metric_name"]): item["freshness_sla_days"] for item in FACT_CONFIG}

    assert set(slas.values()) == {550}
    assert len(slas) == 11


def test_duckdb_engine_routes_packaged_sec_dataset_to_special_materializer(monkeypatch):
    called = {}

    def fake_materializer(engine, ds, user_context):
        called["name"] = ds["name"]
        called["ctx"] = user_context
        return {"name": ds["name"], "layer": "silver", "row_count": 1, "storage_uri": "s3://x"}

    monkeypatch.setattr("app.sec_edgar_materializer.materialize_sec_dataset", fake_materializer, raising=False)
    monkeypatch.setattr("refinement.app.sec_edgar_materializer.materialize_sec_dataset", fake_materializer, raising=False)
    engine = DuckDBEngine()

    result = engine.materialize(
        {"name": "sec_company_metadata_latest", "layer": "silver", "cartridge": "sec_edgar", "sql_def": "SELECT should_not_run"},
        {"tenant_id": "t", "workspace_id": "w"},
    )

    assert result["row_count"] == 1
    assert called == {"name": "sec_company_metadata_latest", "ctx": {"tenant_id": "t", "workspace_id": "w"}}


def test_sec_materializer_rejects_unknown_sec_dataset():
    with pytest.raises(ValueError, match="Unsupported SEC EDGAR dataset"):
        materialize_sec_dataset(
            DuckDBEngine(),
            {"name": "sec_unknown", "layer": "silver", "cartridge": "sec_edgar"},
            {"tenant_id": "t", "workspace_id": "w"},
        )
