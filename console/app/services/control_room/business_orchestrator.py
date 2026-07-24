from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_lineage import item_identity, parent_references
from app.services.control_room.business_projection import (
    eligible_item_ids,
    filter_business_items,
    normalize_persisted_business_item,
)
from app.services.control_room.business_repository import fetch_lineage_rows
from app.services.control_room.business_workflow_provenance import (
    workflow_is_quarantined,
)


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def normalize_orchestrator_source(
    source: Mapping[str, Any],
    *,
    source_type: str,
) -> dict[str, Any]:
    item = dict(source)
    item["metadata"] = _metadata(item.get("metadata"))
    metadata = item["metadata"]
    details = _metadata(metadata.get("details"))
    if "actual_value" not in item and "actual_value" in details:
        item["actual_value"] = details.get("actual_value")
    evidence_pack = metadata.get("evidence_pack")
    if isinstance(evidence_pack, Mapping) and evidence_pack:
        item["evidence_pack"] = dict(evidence_pack)
    elif metadata.get("evidence_pack_id") is not None:
        item["evidence_pack"] = {"id": metadata["evidence_pack_id"]}
    item.setdefault("kind", item.get("item_kind") or source_type)
    item.setdefault("item_kind", item.get("kind") or source_type)
    item.setdefault("source_dataset", item.get("dataset"))
    item.setdefault("cartridge", item.get("cartridge_id") or metadata.get("cartridge"))
    item.setdefault(
        "source_system", metadata.get("source_system") or item.get("cartridge")
    )
    item.setdefault("id", item_identity(item))
    return item


async def eligible_orchestrator_source(
    conn: Any,
    source: Mapping[str, Any],
    *,
    source_type: str,
    tenant_id: str | None,
    workspace_id: str,
    owner_id: int | None = None,
) -> dict[str, Any] | None:
    item = normalize_orchestrator_source(source, source_type=source_type)
    if workflow_is_quarantined(item):
        return None
    refs = parent_references(item)
    lineage_rows = await fetch_lineage_rows(
        conn,
        workspace_id=workspace_id,
        parent_ids=refs.ids,
        tenant_id=tenant_id,
        owner_id=owner_id,
    )
    lineage = [
        normalized
        for row in lineage_rows
        if not workflow_is_quarantined(
            normalized := normalize_persisted_business_item(row)
        )
    ]
    eligible_ids = eligible_item_ids([*lineage, item])
    return item if item_identity(item) in eligible_ids else None


__all__ = ("eligible_orchestrator_source", "normalize_orchestrator_source")
