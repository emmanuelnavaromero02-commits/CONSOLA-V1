from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from omega_lakehouse import LakehouseStorage

SEC_ENTITIES = ("company_metadata", "company_facts")
DEBUG_MARKERS = ("debug", "tmp", "test")


def build_retention_dry_run(
    storage: LakehouseStorage,
    *,
    tenant_id: str,
    workspace_id: str,
    entities: str | list[str] | tuple[str, ...] | None = None,
    keep_latest_per_group: int = 1,
) -> dict[str, Any]:
    if not tenant_id or not workspace_id:
        raise ValueError("tenant_id and workspace_id are required")
    if keep_latest_per_group < 1:
        raise ValueError("keep_latest_per_group must be >= 1")
    selected = _selected_entities(entities)
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    skipped: list[dict[str, str]] = []
    candidates: dict[str, dict[str, Any]] = {}

    for entity in selected:
        prefix = f"raw/sec_edgar/{entity}/tenant_id={tenant_id}/workspace_id={workspace_id}/"
        for stat in storage.iter_list(prefix):
            key = stat.key
            if not key.endswith("/manifest.json"):
                continue
            manifest = _load_manifest(storage, key)
            if not isinstance(manifest, dict):
                skipped.append({"key": key, "reason": "manifest_unreadable"})
                continue
            reason = _invalid_reason(manifest, entity, tenant_id, workspace_id, key)
            if reason:
                skipped.append({"key": key, "reason": reason})
                continue
            item = _manifest_item(manifest, key)
            groups[(item["entity"], item["request_hash"], item["payload_hash"])].append(item)
            if _is_debug_batch(item):
                _add_candidate(candidates, item, "debug_complete_batch")

    for manifests in groups.values():
        ordered = sorted(manifests, key=lambda item: (item["load_date"], item["run_id"], item["manifest_key"]))
        for item in ordered[:-keep_latest_per_group]:
            _add_candidate(candidates, item, "duplicate_complete_batch")

    ordered_candidates = sorted(candidates.values(), key=lambda item: (item["entity"], item["load_date"], item["run_id"]))
    return {
        "dry_run": True,
        "cartridge_id": "sec_edgar",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "entities": list(selected),
        "keep_latest_per_group": keep_latest_per_group,
        "candidate_count": len(ordered_candidates),
        "delete_object_count": 0,
        "delete_prefixes": [item["final_prefix"] for item in ordered_candidates],
        "candidates": ordered_candidates,
        "skipped": skipped,
        "group_count": len(groups),
        "complete_manifest_count": sum(len(items) for items in groups.values()),
    }


def _selected_entities(entities: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if isinstance(entities, str):
        entities = [entities]
    selected = tuple(entities or SEC_ENTITIES)
    invalid = sorted(set(selected) - set(SEC_ENTITIES))
    if invalid:
        raise ValueError(f"unsupported SEC EDGAR entities: {', '.join(invalid)}")
    return selected


def _load_manifest(storage: LakehouseStorage, key: str) -> Any:
    try:
        return json.loads(storage.get_bytes(key).decode("utf-8"))
    except Exception:
        return None


def _invalid_reason(
    manifest: dict[str, Any],
    entity: str,
    tenant_id: str,
    workspace_id: str,
    key: str,
) -> str | None:
    final_prefix = str(manifest.get("final_prefix") or "")
    expected_prefix = f"raw/sec_edgar/{entity}/tenant_id={tenant_id}/workspace_id={workspace_id}/"
    required = ("run_id", "load_date", "request_hash", "payload_hash", "final_prefix")
    if manifest.get("status") != "complete":
        return "manifest_not_complete"
    if manifest.get("cartridge_id") != "sec_edgar":
        return "wrong_cartridge"
    if manifest.get("entity") != entity:
        return "wrong_entity"
    if manifest.get("tenant_id") != tenant_id or manifest.get("workspace_id") != workspace_id:
        return "wrong_scope"
    if any(not manifest.get(field) for field in required):
        return "missing_required_field"
    if not final_prefix.startswith(expected_prefix) or not final_prefix.endswith("/"):
        return "unsafe_final_prefix"
    if key != f"{final_prefix}manifest.json":
        return "manifest_key_mismatch"
    return None


def _manifest_item(manifest: dict[str, Any], key: str) -> dict[str, Any]:
    return {
        "entity": str(manifest["entity"]),
        "manifest_key": key,
        "final_prefix": str(manifest["final_prefix"]),
        "run_id": str(manifest["run_id"]),
        "load_date": str(manifest["load_date"]),
        "request_hash": str(manifest["request_hash"]),
        "payload_hash": str(manifest["payload_hash"]),
        "row_count": int(manifest.get("row_count") or 0),
        "ciks": list(manifest.get("ciks") or []),
    }


def _is_debug_batch(item: dict[str, Any]) -> bool:
    run_id = item["run_id"].lower()
    return any(marker in run_id for marker in DEBUG_MARKERS)


def _add_candidate(candidates: dict[str, dict[str, Any]], item: dict[str, Any], reason: str) -> None:
    current = candidates.get(item["manifest_key"])
    if current:
        reasons = set(current["reasons"])
        reasons.add(reason)
        current["reasons"] = sorted(reasons)
        return
    candidates[item["manifest_key"]] = {**item, "reasons": [reason]}
