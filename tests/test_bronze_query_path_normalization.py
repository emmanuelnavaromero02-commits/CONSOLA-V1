from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-api-key-for-bronze-normalization")

import app.main as console_main


def test_backend_normalizes_bronze_reader_paths_to_s3(monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "modecissions-lakehouse-783792")

    sql = "select * from read_parquet('raw/sap_successfactors/PerPerson') limit 50"

    assert console_main._normalize_bronze_query_sql(sql) == (
        "select * from read_parquet("
        "'s3://modecissions-lakehouse-783792/raw/sap_successfactors/PerPerson/**/*.parquet'"
        ") limit 50"
    )


def test_backend_keeps_bronze_sources_canonical_for_scope():
    assert console_main._normalize_bronze_query_sources([
        "raw/sap_successfactors/PerPerson",
        "s3://lakehouse/raw/sap_successfactors/PerPerson/**/*.parquet",
    ]) == ["raw/sap_successfactors/PerPerson"]
