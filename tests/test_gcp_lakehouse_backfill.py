from __future__ import annotations

import sys
from types import SimpleNamespace

from scripts import gcp_lakehouse_backfill as backfill


def test_backfill_prefix_defaults_are_limited_to_successfactors():
    assert backfill.DEFAULT_PREFIXES == (
        "raw/sap_successfactors/",
        "silver/sap_successfactors/",
        "gold/sap_successfactors/",
    )


def test_backfill_dry_run_plans_without_copying(monkeypatch):
    copied: list[str] = []

    class FakeClient:
        def __init__(self, endpoint, access_key, secret_key, secure):
            self.endpoint = endpoint

        def list_objects(self, bucket, prefix, recursive):
            assert self.endpoint == "source.local:9000"
            assert bucket == "source-lakehouse"
            assert prefix == "raw/sap_successfactors/"
            assert recursive is True
            return [
                SimpleNamespace(object_name="raw/sap_successfactors/User/a.parquet", size=10),
                SimpleNamespace(object_name="raw/sap_successfactors/EmpJob/b.parquet", size=25),
            ]

        def stat_object(self, bucket, key):
            assert self.endpoint == "storage.googleapis.com"
            raise RuntimeError("missing")

        def fget_object(self, bucket, key, filename):
            copied.append(key)

        def fput_object(self, bucket, key, filename, content_type):
            copied.append(key)

    monkeypatch.setitem(sys.modules, "minio", SimpleNamespace(Minio=FakeClient))
    monkeypatch.setenv("SOURCE_MINIO_ENDPOINT", "source.local:9000")
    monkeypatch.setenv("SOURCE_MINIO_BUCKET", "source-lakehouse")
    monkeypatch.setenv("SOURCE_MINIO_ACCESS_KEY", "source-key")
    monkeypatch.setenv("SOURCE_MINIO_SECRET_KEY", "source-secret")
    monkeypatch.setenv("GCS_BUCKET", "omega-gcs-lakehouse")
    monkeypatch.setenv("GCS_ACCESS_KEY_ID", "gcs-key")
    monkeypatch.setenv("GCS_SECRET_ACCESS_KEY", "gcs-secret")

    summary = backfill.run_backfill(
        prefixes=["raw/sap_successfactors/"],
        dry_run=True,
        overwrite=False,
    )

    assert summary["dry_run"] is True
    assert summary["planned"] == 2
    assert summary["copied"] == 0
    assert summary["bytes"] == 35
    assert copied == []
