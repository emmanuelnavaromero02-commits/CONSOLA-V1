from __future__ import annotations

import hmac
import json
import re
from typing import Any

from omega_lakehouse import LakehouseStorage

from app.services.bronze_retention import build_retention_dry_run


_PLAN_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_APPROVAL_REF_RE = re.compile(r"^[A-Za-z0-9_.:/-]{3,160}$")
_MAX_OBJECTS_PER_BATCH = 100
_MAX_TOTAL_OBJECTS = 1000


class RetentionApprovalError(ValueError):
    pass


class RetentionPlanConflict(ValueError):
    pass


def apply_retention_plan(
    storage: LakehouseStorage,
    *,
    tenant_id: str,
    workspace_id: str,
    expected_plan_hash: str,
    approval_ref: str,
    approved: bool,
    entities: str | list[str] | tuple[str, ...] | None = None,
    keep_latest_per_group: int = 1,
    max_objects_per_batch: int = 20,
    max_total_objects: int = 100,
) -> dict[str, Any]:
    _validate_approval(
        approved=approved,
        approval_ref=approval_ref,
        expected_plan_hash=expected_plan_hash,
    )
    if not 1 <= max_objects_per_batch <= _MAX_OBJECTS_PER_BATCH:
        raise ValueError("max_objects_per_batch must be between 1 and 100")
    if not 1 <= max_total_objects <= _MAX_TOTAL_OBJECTS:
        raise ValueError("max_total_objects must be between 1 and 1000")

    report = build_retention_dry_run(
        storage,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        entities=entities,
        keep_latest_per_group=keep_latest_per_group,
    )
    if not hmac.compare_digest(expected_plan_hash, str(report["plan_hash"])):
        raise RetentionPlanConflict("retention plan changed; run dry-run again")

    batches = _preflight_batches(
        storage,
        report["candidates"],
        max_objects_per_batch=max_objects_per_batch,
        max_total_objects=max_total_objects,
    )
    deleted: list[dict[str, Any]] = []
    deleted_objects = 0
    for candidate, keys in batches:
        manifest_key = str(candidate["manifest_key"])
        for key in (key for key in keys if key != manifest_key):
            if not storage.delete_object(key):
                raise RetentionPlanConflict("retention batch changed during deletion")
            deleted_objects += 1

        remaining = _keys_for_prefix(storage, str(candidate["final_prefix"]))
        if remaining != [manifest_key]:
            raise RetentionPlanConflict(
                "retention batch changed during deletion; manifest was preserved"
            )
        if not storage.delete_object(manifest_key):
            raise RetentionPlanConflict("retention manifest changed during deletion")
        deleted_objects += 1
        if _keys_for_prefix(storage, str(candidate["final_prefix"])):
            raise RetentionPlanConflict("retention prefix is not empty after deletion")
        deleted.append(
            {
                "entity": candidate["entity"],
                "run_id": candidate["run_id"],
                "final_prefix": candidate["final_prefix"],
                "reasons": candidate["reasons"],
                "deleted_objects": len(keys),
            }
        )

    return {
        "dry_run": False,
        "status": "complete",
        "cartridge_id": "sec_edgar",
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "approval_ref": approval_ref,
        "plan_hash": report["plan_hash"],
        "deleted_batch_count": len(deleted),
        "deleted_object_count": deleted_objects,
        "deleted": deleted,
    }


def _validate_approval(*, approved: bool, approval_ref: str, expected_plan_hash: str) -> None:
    if approved is not True:
        raise RetentionApprovalError("explicit retention approval is required")
    if not _APPROVAL_REF_RE.fullmatch(approval_ref or ""):
        raise RetentionApprovalError("valid approval_ref is required")
    if not _PLAN_HASH_RE.fullmatch(expected_plan_hash or ""):
        raise RetentionApprovalError("valid expected_plan_hash is required")


def _preflight_batches(
    storage: LakehouseStorage,
    candidates: list[dict[str, Any]],
    *,
    max_objects_per_batch: int,
    max_total_objects: int,
) -> list[tuple[dict[str, Any], list[str]]]:
    batches: list[tuple[dict[str, Any], list[str]]] = []
    total_objects = 0
    for candidate in candidates:
        _assert_manifest_unchanged(storage, candidate)
        keys = _keys_for_prefix(storage, str(candidate["final_prefix"]))
        if str(candidate["manifest_key"]) not in keys:
            raise RetentionPlanConflict("retention manifest disappeared; run dry-run again")
        if len(keys) > max_objects_per_batch:
            raise RetentionPlanConflict("retention batch exceeds max_objects_per_batch")
        total_objects += len(keys)
        if total_objects > max_total_objects:
            raise RetentionPlanConflict("retention plan exceeds max_total_objects")
        batches.append((candidate, keys))
    return batches


def _assert_manifest_unchanged(
    storage: LakehouseStorage,
    candidate: dict[str, Any],
) -> None:
    try:
        manifest = json.loads(
            storage.get_bytes(str(candidate["manifest_key"])).decode("utf-8")
        )
    except Exception as exc:
        raise RetentionPlanConflict("retention manifest is no longer readable") from exc
    expected = {
        "status": "complete",
        "cartridge_id": "sec_edgar",
        "entity": candidate["entity"],
        "run_id": candidate["run_id"],
        "load_date": candidate["load_date"],
        "request_hash": candidate["request_hash"],
        "payload_hash": candidate["payload_hash"],
        "final_prefix": candidate["final_prefix"],
    }
    if not isinstance(manifest, dict) or any(
        manifest.get(field) != value for field, value in expected.items()
    ):
        raise RetentionPlanConflict("retention manifest changed; run dry-run again")


def _keys_for_prefix(storage: LakehouseStorage, prefix: str) -> list[str]:
    return sorted(obj.key for obj in storage.iter_list(prefix))
