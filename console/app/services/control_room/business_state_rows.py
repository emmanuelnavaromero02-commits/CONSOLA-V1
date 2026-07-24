from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from app.services.control_room.business_eligibility import (
    BUSINESS_EVIDENCE_FIELDS,
    BUSINESS_MATERIALIZATION_FIELDS,
    BUSINESS_OBSERVATION_FIELDS,
)
from app.services.control_room.business_observation_codec import (
    with_observation_envelope,
)
from app.services.control_room.business_policy_metadata import business_policy_metadata
from app.services.control_room.business_source_scope import canonical_scoped_item
from app.services.control_room.business_projection import (
    eligible_item_ids,
    strip_business_fields,
)


MetadataBuilder = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
DiagnosticBuilder = Callable[[dict[str, Any]], dict[str, Any]]
ImpactBuilder = Callable[..., dict[str, Any]]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _stable_payload(item: Mapping[str, Any]) -> str:
    return json.dumps(dict(item), default=str, separators=(",", ":"), sort_keys=True)


def canonical_items(
    items: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in items:
        item = dict(raw)
        item_id = str(item.get("id") or item.get("item_id") or "").strip()
        if item_id:
            grouped[item_id].append(item)
    duplicate_ids = {item_id for item_id, rows in grouped.items() if len(rows) > 1}
    selected = [
        min(rows, key=_stable_payload) for item_id, rows in sorted(grouped.items())
    ]
    return selected, duplicate_ids


def _row(
    item: Mapping[str, Any],
    *,
    tenant_id: str | None,
    workspace_id: str,
    owner_user_id: int | None,
    metadata: Mapping[str, Any],
    impact: Mapping[str, Any],
    status: str | None = None,
) -> dict[str, Any]:
    row = {
        "tenant_id": tenant_id or "",
        "workspace_id": workspace_id,
        "owner_user_id": owner_user_id,
        "item_id": str(item.get("id") or item.get("item_id") or ""),
        "cartridge_id": item.get("cartridge") or item.get("cartridge_id"),
        "domain": item.get("domain"),
        "source_dataset": item.get("source_dataset"),
        "item_kind": item.get("kind") or item.get("item_kind"),
        "title": item.get("title"),
        "severity": item.get("severity"),
        "entity_kind": item.get("entity_kind"),
        "entity_id": item.get("entity_id"),
        "entity_label": item.get("entity_label"),
        "anomaly_type": item.get("anomaly_type"),
        "metadata": dict(metadata),
        "impact_estimate": impact.get("estimate"),
        "impact_currency": impact.get("currency") or "USD",
        "confidence": impact.get("confidence"),
        "priority_score": impact.get("priority_score") or 0,
        "selected_option_id": item.get("selected_option_id"),
        "execution_status": item.get("execution_status") or "not_started",
    }
    if status is not None:
        row["status"] = status
    return row


def state_rows(
    items: Iterable[Mapping[str, Any]],
    *,
    tenant_id: str | None,
    workspace_id: str,
    owner_user_id: int | None,
    impact_builder: ImpactBuilder,
    metadata_builder: MetadataBuilder,
    diagnostic_builder: DiagnosticBuilder,
    owner_by_item: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    selected, duplicate_ids = canonical_items(items)
    selected = [
        canonical_scoped_item(
            item,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        for item in selected
    ]
    business_ids = eligible_item_ids(
        item for item in selected if str(item.get("id") or "") not in duplicate_ids
    )
    rows: list[dict[str, Any]] = []
    for item in selected:
        item_id = str(item.get("id") or "")
        eligible = item_id in business_ids
        persisted = (
            {**item, "data_status": "invalid_schema"}
            if item_id in duplicate_ids
            else item
        )
        impact = (
            impact_builder(persisted, eligible_parent_ids=business_ids)
            if eligible
            else {}
        )
        metadata = (
            business_policy_metadata(metadata_builder(persisted, impact), persisted)
            if eligible
            else diagnostic_builder(persisted)
        )
        row_owner_id = dict(owner_by_item or {}).get(item_id, owner_user_id)
        rows.append(
            _row(
                (
                    persisted
                    if eligible
                    else {
                        **persisted,
                        "selected_option_id": None,
                        "execution_status": "not_started",
                    }
                ),
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                owner_user_id=row_owner_id,
                metadata=metadata,
                impact=impact,
            )
        )
    return rows


def diagnostic_metadata(item: Mapping[str, Any]) -> dict[str, Any]:
    existing = strip_business_fields(_mapping(item.get("metadata")))
    existing_details = strip_business_fields(_mapping(existing.get("details")))
    item_details = strip_business_fields(_mapping(item.get("details")))
    metadata = {
        **existing,
        "module": item.get("module"),
        "details": {**existing_details, **item_details},
        "source_system": item.get("source_system"),
    }
    for key in (
        "data_status",
        "item_kind",
        "data_readiness",
        "evaluation_status",
        "readiness_status",
        "source_status",
        "parent_item_id",
        "source_item_id",
        "derived_from",
        *BUSINESS_OBSERVATION_FIELDS,
        *BUSINESS_MATERIALIZATION_FIELDS,
    ):
        if key in item and not (
            key == "item_kind"
            and str(metadata.get(key) or "").strip().lower() == "source_state"
        ):
            metadata[key] = item.get(key)
    if isinstance(item.get("lineage"), Mapping) and item["lineage"]:
        metadata["lineage"] = item["lineage"]
    for key in BUSINESS_EVIDENCE_FIELDS:
        value = item.get(key)
        if isinstance(value, (dict, list, tuple)) and value:
            metadata[key] = value
    return with_observation_envelope(metadata, item)


def ensured_row(
    item: Mapping[str, Any],
    *,
    tenant_id: str | None,
    workspace_id: str,
    owner_user_id: int | None,
    status: str,
    impact: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    return _row(
        item,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        metadata=metadata,
        impact=impact,
        status=status,
    )


__all__ = ("canonical_items", "diagnostic_metadata", "ensured_row", "state_rows")
