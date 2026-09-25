#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

from omega_lakehouse import storage_from_env


ENTITIES = ("company_metadata", "company_facts")


@dataclass(frozen=True)
class SecBatch:
    entity: str
    tenant_id: str
    workspace_id: str
    load_date: str
    run_id: str
    request_hash: str
    payload_hash: str
    final_prefix: str
    manifest_key: str
    reason: str = ""


def _manifest(storage: Any, key: str) -> dict[str, Any]:
    try:
        data = json.loads(storage.get_bytes(key).decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _batch_from_manifest(key: str, manifest: dict[str, Any]) -> SecBatch | None:
    entity = str(manifest.get("entity") or "")
    final_prefix = str(manifest.get("final_prefix") or "")
    if (
        manifest.get("status") != "complete"
        or manifest.get("cartridge_id") != "sec_edgar"
        or entity not in ENTITIES
        or not key.endswith("/manifest.json")
        or not final_prefix.startswith(f"raw/sec_edgar/{entity}/")
    ):
        return None
    return SecBatch(
        entity=entity,
        tenant_id=str(manifest.get("tenant_id") or ""),
        workspace_id=str(manifest.get("workspace_id") or ""),
        load_date=str(manifest.get("load_date") or ""),
        run_id=str(manifest.get("run_id") or ""),
        request_hash=str(manifest.get("request_hash") or ""),
        payload_hash=str(manifest.get("payload_hash") or ""),
        final_prefix=final_prefix,
        manifest_key=key,
    )


def load_batches(storage: Any) -> list[SecBatch]:
    batches: list[SecBatch] = []
    for entity in ENTITIES:
        for obj in storage.iter_list(f"raw/sec_edgar/{entity}/"):
            key = str(getattr(obj, "key", "") or "")
            if not key.endswith("/manifest.json"):
                continue
            batch = _batch_from_manifest(key, _manifest(storage, key))
            if batch:
                batches.append(batch)
    return sorted(batches, key=lambda item: (item.entity, item.load_date, item.run_id, item.manifest_key))


def plan_candidates(batches: list[SecBatch], *, keep_latest_per_hash: int = 1) -> list[SecBatch]:
    grouped: dict[tuple[str, str, str, str, str], list[SecBatch]] = {}
    candidates: dict[str, SecBatch] = {}
    for batch in batches:
        if "debug" in batch.run_id.lower() or "debug" in batch.manifest_key.lower():
            candidates[batch.final_prefix] = SecBatch(**{**asdict(batch), "reason": "debug_batch"})
        key = (batch.entity, batch.tenant_id, batch.workspace_id, batch.request_hash, batch.payload_hash)
        grouped.setdefault(key, []).append(batch)

    keep = max(1, int(keep_latest_per_hash or 1))
    for group in grouped.values():
        if len(group) <= keep:
            continue
        for batch in sorted(group, key=lambda item: (item.load_date, item.run_id, item.manifest_key))[:-keep]:
            candidates.setdefault(
                batch.final_prefix,
                SecBatch(**{**asdict(batch), "reason": "duplicate_complete_batch"}),
            )
    return sorted(candidates.values(), key=lambda item: (item.entity, item.load_date, item.run_id))


def execute_delete(storage: Any, candidates: list[SecBatch], *, execute: bool, confirm: bool, max_objects: int) -> list[dict[str, Any]]:
    if execute and not confirm:
        raise SystemExit("--execute requires --confirm-delete-sec-bronze")
    results = []
    for item in candidates:
        deleted = 0
        if execute:
            deleted = storage.delete_prefix(item.final_prefix, require_trailing_slash=True, max_objects=max_objects)
        results.append({**asdict(item), "deleted_objects": deleted, "executed": execute})
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-delete-sec-bronze", action="store_true")
    parser.add_argument("--keep-latest-per-hash", type=int, default=1)
    parser.add_argument("--max-objects", type=int, default=1000)
    args = parser.parse_args()

    storage = storage_from_env()
    batches = load_batches(storage)
    candidates = plan_candidates(batches, keep_latest_per_hash=args.keep_latest_per_hash)
    result = {
        "mode": "execute" if args.execute else "dry_run",
        "batch_count": len(batches),
        "candidate_count": len(candidates),
        "candidates": execute_delete(
            storage,
            candidates,
            execute=args.execute,
            confirm=args.confirm_delete_sec_bronze,
            max_objects=args.max_objects,
        ),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
