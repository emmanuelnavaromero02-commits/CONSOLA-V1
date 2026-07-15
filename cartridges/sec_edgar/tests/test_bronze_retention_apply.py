from __future__ import annotations

import json

import pytest

from omega_lakehouse import ObjectNotFound
from omega_lakehouse.types import ObjectStat

from app.services.bronze_retention import build_retention_dry_run
from app.services.bronze_retention_apply import (
    RetentionApprovalError,
    RetentionPlanConflict,
    apply_retention_plan,
)


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.fail_on: str | None = None

    def get_bytes(self, key):
        if key not in self.objects:
            raise ObjectNotFound("missing", key=key)
        return self.objects[key]

    def iter_list(self, prefix, *, page_size=1000):
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield ObjectStat(key, f"s3://lakehouse/{key}", len(self.objects[key]))

    def delete_object(self, key, *, expected_version=None):
        if key == self.fail_on:
            raise RuntimeError("interrupted")
        if key not in self.objects:
            return False
        del self.objects[key]
        self.deleted.append(key)
        return True


def test_apply_deletes_only_approved_plan_and_manifest_last():
    storage = _duplicate_storage()
    report = _report(storage)

    result = _apply(storage, report["plan_hash"])

    old_prefix = _prefix("2026-07-01", "run-old")
    new_prefix = _prefix("2026-07-02", "run-new")
    assert result["status"] == "complete"
    assert result["deleted_batch_count"] == 1
    assert result["deleted_object_count"] == 3
    assert not any(key.startswith(old_prefix) for key in storage.objects)
    assert any(key.startswith(new_prefix) for key in storage.objects)
    assert storage.deleted[-1] == f"{old_prefix}manifest.json"


def test_apply_requires_explicit_approval_and_matching_plan():
    storage = _duplicate_storage()
    report = _report(storage)

    with pytest.raises(RetentionApprovalError):
        apply_retention_plan(
            storage,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            expected_plan_hash=report["plan_hash"],
            approval_ref="PRD-TEST",
            approved=False,
        )

    storage.objects[f"{_prefix('2026-07-01', 'run-old')}manifest.json"] = _manifest_bytes(
        "2026-07-01", "run-old", payload_hash="changed"
    )
    with pytest.raises(RetentionPlanConflict, match="plan changed"):
        _apply(storage, report["plan_hash"])
    assert storage.deleted == []


def test_apply_checks_global_limit_before_deleting():
    storage = _duplicate_storage()
    report = _report(storage)

    with pytest.raises(RetentionPlanConflict, match="max_total_objects"):
        _apply(storage, report["plan_hash"], max_total_objects=2)

    assert storage.deleted == []

    with pytest.raises(ValueError, match="between 1 and 1000"):
        _apply(storage, report["plan_hash"], max_total_objects=1001)


def test_interrupted_delete_preserves_complete_manifest():
    storage = _duplicate_storage()
    report = _report(storage)
    old_prefix = _prefix("2026-07-01", "run-old")
    storage.fail_on = f"{old_prefix}source_response.json"

    with pytest.raises(RuntimeError, match="interrupted"):
        _apply(storage, report["plan_hash"])

    assert f"{old_prefix}manifest.json" in storage.objects


def _apply(storage: MemoryStorage, plan_hash: str, **kwargs):
    return apply_retention_plan(
        storage,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        expected_plan_hash=plan_hash,
        approval_ref="PRD-TEST",
        approved=True,
        entities=["company_facts"],
        **kwargs,
    )


def _report(storage: MemoryStorage):
    return build_retention_dry_run(
        storage,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        entities=["company_facts"],
    )


def _duplicate_storage() -> MemoryStorage:
    storage = MemoryStorage()
    for load_date, run_id in (("2026-07-01", "run-old"), ("2026-07-02", "run-new")):
        prefix = _prefix(load_date, run_id)
        storage.objects[f"{prefix}manifest.json"] = _manifest_bytes(load_date, run_id)
        storage.objects[f"{prefix}series.parquet"] = b"parquet"
        storage.objects[f"{prefix}source_response.json"] = b"{}"
    return storage


def _prefix(load_date: str, run_id: str) -> str:
    return (
        "raw/sec_edgar/company_facts/tenant_id=tenant-a/workspace_id=workspace-a/"
        f"load_date={load_date}/batch_id={run_id}/"
    )


def _manifest_bytes(
    load_date: str,
    run_id: str,
    *,
    payload_hash: str = "payload-a",
) -> bytes:
    prefix = _prefix(load_date, run_id)
    return json.dumps(
        {
            "status": "complete",
            "cartridge_id": "sec_edgar",
            "entity": "company_facts",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "load_date": load_date,
            "run_id": run_id,
            "request_hash": "request-a",
            "payload_hash": payload_hash,
            "final_prefix": prefix,
            "row_count": 1,
            "ciks": ["0000000000"],
        }
    ).encode("utf-8")
