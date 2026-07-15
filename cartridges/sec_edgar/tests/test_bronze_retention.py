from __future__ import annotations

import json

from omega_lakehouse import ObjectNotFound
from omega_lakehouse.types import ObjectStat

from app.services.bronze_retention import build_retention_dry_run


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def get_bytes(self, key):
        if key not in self.objects:
            raise ObjectNotFound("missing", key=key)
        return self.objects[key]

    def iter_list(self, prefix, *, page_size=1000):
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield ObjectStat(key, f"s3://lakehouse/{key}", len(self.objects[key]))


def test_retention_dry_run_reports_only_complete_duplicate_and_debug_batches():
    storage = MemoryStorage()
    _put_manifest(storage, "company_facts", "tenant-a", "workspace-a", "2026-07-01", "run-old", "req-a", "payload-a")
    _put_manifest(storage, "company_facts", "tenant-a", "workspace-a", "2026-07-02", "run-new", "req-a", "payload-a")
    _put_manifest(storage, "company_facts", "tenant-a", "workspace-a", "2026-07-03", "debug-run", "req-b", "payload-b")
    _put_manifest(storage, "company_facts", "tenant-a", "workspace-a", "2026-07-04", "pending", "req-c", "payload-c", status="pending")
    storage.objects[
        "raw/sec_edgar/company_facts/tenant_id=tenant-a/workspace_id=workspace-a/load_date=2026-07-05/batch_id=bad/manifest.json"
    ] = b"{not-json"

    report = build_retention_dry_run(storage, tenant_id="tenant-a", workspace_id="workspace-a")

    assert report["dry_run"] is True
    assert len(report["plan_hash"]) == 64
    assert report["delete_object_count"] == 0
    assert report["candidate_count"] == 2
    by_run = {item["run_id"]: item for item in report["candidates"]}
    assert by_run["run-old"]["reasons"] == ["duplicate_complete_batch"]
    assert by_run["debug-run"]["reasons"] == ["debug_complete_batch"]
    assert {item["reason"] for item in report["skipped"]} == {"manifest_not_complete", "manifest_unreadable"}


def test_retention_dry_run_is_scoped_by_tenant_and_workspace():
    storage = MemoryStorage()
    _put_manifest(storage, "company_metadata", "tenant-a", "workspace-a", "2026-07-01", "run-old", "req-a", "payload-a")
    _put_manifest(storage, "company_metadata", "tenant-a", "workspace-a", "2026-07-02", "run-new", "req-a", "payload-a")
    _put_manifest(storage, "company_metadata", "tenant-a", "workspace-b", "2026-07-01", "run-other", "req-a", "payload-a")

    report = build_retention_dry_run(storage, tenant_id="tenant-a", workspace_id="workspace-a", entities=["company_metadata"])

    assert report["candidate_count"] == 1
    assert report["candidates"][0]["run_id"] == "run-old"
    assert "workspace-b" not in report["candidates"][0]["final_prefix"]


def test_retention_dry_run_rejects_unknown_entities():
    storage = MemoryStorage()

    try:
        build_retention_dry_run(storage, tenant_id="tenant-a", workspace_id="workspace-a", entities=["bad"])
    except ValueError as exc:
        assert "unsupported SEC EDGAR entities" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_retention_dry_run_rejects_unsafe_scope():
    storage = MemoryStorage()

    try:
        build_retention_dry_run(
            storage,
            tenant_id="tenant-a/other",
            workspace_id="workspace-a",
        )
    except ValueError as exc:
        assert "invalid tenant_id" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def _put_manifest(
    storage: MemoryStorage,
    entity: str,
    tenant_id: str,
    workspace_id: str,
    load_date: str,
    run_id: str,
    request_hash: str,
    payload_hash: str,
    *,
    status: str = "complete",
) -> None:
    final_prefix = (
        f"raw/sec_edgar/{entity}/tenant_id={tenant_id}/workspace_id={workspace_id}/"
        f"load_date={load_date}/batch_id={run_id}/"
    )
    manifest = {
        "status": status,
        "cartridge_id": "sec_edgar",
        "entity": entity,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "load_date": load_date,
        "run_id": run_id,
        "request_hash": request_hash,
        "payload_hash": payload_hash,
        "final_prefix": final_prefix,
        "row_count": 1,
        "ciks": ["0000000000"],
    }
    storage.objects[f"{final_prefix}manifest.json"] = json.dumps(manifest).encode("utf-8")
