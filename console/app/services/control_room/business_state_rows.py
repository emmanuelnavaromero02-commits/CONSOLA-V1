from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from app.services.control_room.business_projection import eligible_item_ids


MetadataBuilder = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
DiagnosticBuilder = Callable[[dict[str, Any]], dict[str, Any]]
ImpactBuilder = Callable[..., dict[str, Any]]


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
        "impact_currency": impact.get("currency") or ("USD" if impact else None),
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
) -> list[dict[str, Any]]:
    selected, duplicate_ids = canonical_items(items)
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
            metadata_builder(persisted, impact)
            if eligible
            else diagnostic_builder(persisted)
        )
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
                owner_user_id=owner_user_id,
                metadata=metadata,
                impact=impact,
            )
        )
    return rows


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


__all__ = ("canonical_items", "ensured_row", "state_rows")
