from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from scripts import sec_bronze_retention as retention


class FakeStorage:
    def __init__(self, manifests: dict[str, dict]):
        self.manifests = manifests
        self.deleted: list[tuple[str, int | None]] = []

    def iter_list(self, prefix: str):
        for key in sorted(self.manifests):
            if key.startswith(prefix):
                yield SimpleNamespace(key=key)

    def get_bytes(self, key: str) -> bytes:
        return json.dumps(self.manifests[key]).encode("utf-8")

    def delete_prefix(self, prefix: str, *, require_trailing_slash: bool = True, max_objects: int | None = None) -> int:
        assert require_trailing_slash is True
        assert prefix.endswith("/")
        self.deleted.append((prefix, max_objects))
        return 3


def _manifest(entity: str, run_id: str, *, status: str = "complete", payload_hash: str = "payload"):
    prefix = f"raw/sec_edgar/{entity}/tenant_id=t/workspace_id=w/load_date=2026-07-13/batch_id={run_id}/"
    return {
        "status": status,
        "cartridge_id": "sec_edgar",
        "entity": entity,
        "tenant_id": "t",
        "workspace_id": "w",
        "load_date": "2026-07-13",
        "run_id": run_id,
        "request_hash": "request",
        "payload_hash": payload_hash,
        "final_prefix": prefix,
    }


def test_sec_retention_plans_only_complete_duplicate_and_debug_batches():
    manifests = {
        "raw/sec_edgar/company_facts/tenant_id=t/workspace_id=w/load_date=2026-07-13/batch_id=a/manifest.json": _manifest("company_facts", "a"),
        "raw/sec_edgar/company_facts/tenant_id=t/workspace_id=w/load_date=2026-07-13/batch_id=b/manifest.json": _manifest("company_facts", "b"),
        "raw/sec_edgar/company_facts/tenant_id=t/workspace_id=w/load_date=2026-07-13/batch_id=codex-sec-debug-1/manifest.json": _manifest("company_facts", "codex-sec-debug-1", payload_hash="debug"),
        "raw/sec_edgar/company_metadata/tenant_id=t/workspace_id=w/load_date=2026-07-13/batch_id=pending/manifest.json": _manifest("company_metadata", "pending", status="pending"),
    }
    batches = retention.load_batches(FakeStorage(manifests))
    candidates = retention.plan_candidates(batches)

    assert len(batches) == 3
    assert [item.run_id for item in candidates] == ["a", "codex-sec-debug-1"]
    assert {item.reason for item in candidates} == {"duplicate_complete_batch", "debug_batch"}


def test_sec_retention_delete_requires_explicit_confirm():
    batch = retention.SecBatch(
        entity="company_facts",
        tenant_id="t",
        workspace_id="w",
        load_date="2026-07-13",
        run_id="a",
        request_hash="request",
        payload_hash="payload",
        final_prefix="raw/sec_edgar/company_facts/tenant_id=t/workspace_id=w/load_date=2026-07-13/batch_id=a/",
        manifest_key="raw/sec_edgar/company_facts/tenant_id=t/workspace_id=w/load_date=2026-07-13/batch_id=a/manifest.json",
        reason="duplicate_complete_batch",
    )
    storage = FakeStorage({})

    with pytest.raises(SystemExit):
        retention.execute_delete(storage, [batch], execute=True, confirm=False, max_objects=10)

    result = retention.execute_delete(storage, [batch], execute=True, confirm=True, max_objects=10)

    assert storage.deleted == [(batch.final_prefix, 10)]
    assert result[0]["deleted_objects"] == 3
