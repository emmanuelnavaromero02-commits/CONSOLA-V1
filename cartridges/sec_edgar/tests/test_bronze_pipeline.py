from __future__ import annotations

import hashlib
import io

import pytest

from omega_lakehouse import ObjectAlreadyExists, ObjectNotFound
from omega_lakehouse.types import ListPage, ObjectStat, PublishResult, PutResult

from app.services.config_loader import load_company_configs
from app.services.extraction_service import run_company_facts


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str], str]] = {}
        self.fail_manifest_once = False

    def put_bytes(self, key, data, *, overwrite=False, metadata=None, checksum_sha256=None, **_):
        if not overwrite and key in self.objects:
            raise ObjectAlreadyExists("exists", key=key)
        self.objects[key] = (data, metadata or {}, checksum_sha256 or _sha(data))
        return PutResult(key, self.uri_for(key), len(data), checksum_sha256 or _sha(data))

    def put_file(self, key, path, **kwargs):
        return self.put_bytes(key, path.read_bytes(), **kwargs)

    def get_bytes(self, key):
        if key not in self.objects:
            raise ObjectNotFound("missing", key=key)
        return self.objects[key][0]

    def open_reader(self, key):
        return io.BytesIO(self.get_bytes(key))

    def iter_chunks(self, key, *, chunk_size=1024 * 1024):
        yield self.get_bytes(key)

    def stat(self, key):
        if key not in self.objects:
            raise ObjectNotFound("missing", key=key)
        body, metadata, checksum = self.objects[key]
        return ObjectStat(key, self.uri_for(key), len(body), checksum_sha256=checksum, metadata=metadata)

    def exists(self, key):
        return key in self.objects

    def copy(self, source_key, target_key, **_):
        body, metadata, checksum = self.objects[source_key]
        self.objects[target_key] = (body, metadata, checksum)
        return PutResult(target_key, self.uri_for(target_key), len(body), checksum)

    def publish(self, staging_key, final_key, *, expected_sha256=None, delete_staging=True, **_):
        if self.fail_manifest_once and final_key.endswith("manifest.json"):
            self.fail_manifest_once = False
            raise RuntimeError("crash before manifest")
        if final_key in self.objects:
            raise ObjectAlreadyExists("exists", key=final_key)
        body, metadata, checksum = self.objects[staging_key]
        assert expected_sha256 in (None, checksum)
        self.objects[final_key] = (body, metadata, checksum)
        if delete_staging:
            self.objects.pop(staging_key, None)
        return PublishResult(staging_key, final_key, self.uri_for(final_key), checksum, staging_deleted=True)

    def iter_list(self, prefix, *, page_size=1000):
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield self.stat(key)

    def list_page(self, prefix, *, page_size=1000, cursor=None):
        return ListPage(tuple(self.iter_list(prefix)))

    def uri_for(self, key):
        return f"s3://lakehouse/{key}"


class FakeClient:
    def get_metadata(self, ciks):
        configs = {item.cik: item for item in load_company_configs()}
        return {"sec_edgar": {"metadata": [_metadata(configs[cik]) for cik in ciks]}}

    def get_company_facts(self, ciks):
        configs = {item.cik: item for item in load_company_configs()}
        return {"sec_edgar": {"companies": [_facts(configs[cik]) for cik in ciks]}}


class FakeWatermarks:
    def __init__(self) -> None:
        self.updated: dict[str, str] = {}

    def get_many(self, _tenant_id, _workspace_id, keys):
        return {key: None for key in keys}

    def update_many(self, _tenant_id, _workspace_id, values, *, run_id):
        self.updated.update(values)


def test_bronze_layout_manifest_provenance_and_watermark():
    storage = MemoryStorage()
    watermarks = FakeWatermarks()
    result = run_company_facts(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        run_id="run-a",
        client=FakeClient(),
        storage=storage,
        watermarks=watermarks,
    )
    assert result["status"] == "success"
    assert result["record_count"] == 11
    assert any("raw/sec_edgar/company_facts/" in key and key.endswith(".parquet") for key in storage.objects)
    assert any(key.endswith("manifest.json") for key in storage.objects)
    assert watermarks.updated


def test_recovery_after_parquet_before_manifest_does_not_advance_watermark_first(monkeypatch):
    # Force the wall clock to advance between the interrupted write and the
    # recovery so the per-run _retrieved_at stamp differs, deterministically
    # exercising the immutable-batch recovery/adopt path (a same-second run would
    # pass by luck and hide a regression).
    import itertools

    from app.services import bronze_records, extraction_service

    _clock = itertools.count()
    monkeypatch.setattr(
        bronze_records,
        "utc_now_iso",
        lambda: f"2026-08-26T00:00:{next(_clock):02d}Z",
    )
    monkeypatch.setattr(
        extraction_service,
        "utc_now_iso",
        lambda: f"2026-08-26T00:00:{next(_clock):02d}Z",
    )

    storage = MemoryStorage()
    storage.fail_manifest_once = True
    watermarks = FakeWatermarks()
    with pytest.raises(RuntimeError):
        run_company_facts(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            run_id="run-recover",
            client=FakeClient(),
            storage=storage,
            watermarks=watermarks,
        )
    assert not watermarks.updated
    assert any(key.endswith(".parquet") for key in storage.objects)
    assert not any(key.startswith("raw/sec_edgar/company_") and key.endswith("manifest.json") for key in storage.objects)

    result = run_company_facts(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        run_id="run-recover",
        client=FakeClient(),
        storage=storage,
        watermarks=watermarks,
    )
    assert result["status"] == "success"
    assert watermarks.updated


def _metadata(company):
    return {
        "cik": company.cik,
        "name": company.expected_name,
        "tickers": [company.ticker],
        "exchanges": ["NYSE"],
        "entity_type": company.entity_type,
        "sic": company.sic,
        "sic_description": company.sic_description,
    }


def _facts(company):
    facts = {}
    for fact in company.facts:
        facts.setdefault(fact.taxonomy, {})[fact.concept] = {
            "units": {fact.unit: [{"end": "2025-12-31", "val": 100, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2026-02-01", "accn": "abc"}]}
        }
    return {"cik": company.cik, "raw_facts": {"facts": facts}}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
