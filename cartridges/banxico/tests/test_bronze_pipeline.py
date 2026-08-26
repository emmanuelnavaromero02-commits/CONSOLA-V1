from __future__ import annotations

import io
from datetime import date

import pytest

from omega_lakehouse import ObjectAlreadyExists, ObjectNotFound
from omega_lakehouse.types import ListPage, ObjectStat, PublishResult, PutResult

from app.services.config_loader import load_series_configs
from app.services.date_windows import build_windows
from app.services.extraction_service import run_series_observations


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

    def delete_object(self, key, **_):
        return self.objects.pop(key, None) is not None

    def delete_prefix(self, prefix, **_):
        keys = [key for key in self.objects if key.startswith(prefix)]
        for key in keys:
            self.objects.pop(key, None)
        return len(keys)

    def iter_list(self, prefix, *, page_size=1000):
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield self.stat(key)

    def list_page(self, prefix, *, page_size=1000, cursor=None):
        return ListPage(tuple(self.iter_list(prefix)))

    def uri_for(self, key):
        return f"s3://lakehouse/{key}"


class FakeClient:
    def __init__(self) -> None:
        self.metadata_payload = {
            "bmx": {
                "series": [
                    {"idSerie": item.series_id, "titulo": item.expected_title}
                    for item in load_series_configs()
                ]
            }
        }

    def get_metadata(self, _ids):
        return self.metadata_payload

    def get_observations(self, ids, _from_date, _to_date):
        return {
            "bmx": {
                "series": [
                    {
                        "idSerie": series_id,
                        "datos": [{"fecha": "10/07/2026", "dato": "18.25"}],
                    }
                    for series_id in ids
                ]
            }
        }


class FakeWatermarks:
    def __init__(self, values=None) -> None:
        self.values = dict(values or {})
        self.updated: dict[str, str] = {}

    def get_many(self, _tenant_id, _workspace_id, series_ids):
        return {series_id: self.values.get(series_id) for series_id in series_ids}

    def update_many(self, _tenant_id, _workspace_id, values, *, run_id):
        self.updated.update(values)


def test_bronze_layout_manifest_provenance_and_watermark():
    storage = MemoryStorage()
    watermarks = FakeWatermarks()
    result = run_series_observations(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        run_id="run-a",
        client=FakeClient(),
        storage=storage,
        watermarks=watermarks,
    )
    assert result["status"] == "success"
    manifests = [key for key in storage.objects if key.endswith("manifest.json")]
    assert manifests
    assert all("/tenant_id=tenant-a/workspace_id=workspace-a/" in key for key in manifests)
    assert watermarks.updated
    parquet = [key for key in storage.objects if key.endswith(".parquet")]
    assert any("raw/banxico/series_observations/" in key for key in parquet)


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
        run_series_observations(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            run_id="run-recover",
            client=FakeClient(),
            storage=storage,
            watermarks=watermarks,
        )
    assert not watermarks.updated
    assert any(key.endswith(".parquet") for key in storage.objects)
    assert not any(
        key.startswith("raw/banxico/series_") and key.endswith("manifest.json")
        for key in storage.objects
    )

    result = run_series_observations(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        run_id="run-recover",
        client=FakeClient(),
        storage=storage,
        watermarks=watermarks,
    )
    assert result["status"] == "success"
    assert watermarks.updated
    assert any(key.endswith("manifest.json") for key in storage.objects)


def test_series_with_different_frequency_do_not_share_watermark_window():
    daily, event = load_series_configs()[0], load_series_configs()[1]
    windows = build_windows(
        (daily, event),
        {daily.series_id: "2026-07-10", event.series_id: "2026-07-10"},
        mode="incremental",
        from_date=None,
        to_date=date(2026, 7, 10),
        default_start=date(2018, 1, 1),
    )
    assert len(windows) == 2
    assert {window.series[0].frequency for window in windows} == {"daily", "event"}


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()
