from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from app.services.control_room.business_eligibility import classify_business_item


BUSINESS_ONLY_FIELDS = frozenset(
    {
        "action_templates",
        "alert",
        "alert_state",
        "control_state",
        "decision_id",
        "execution_status",
        "impact_drivers",
        "impact_estimate",
        "impact_formula",
        "omega",
        "options",
        "priority",
        "priority_score",
        "recommended_actions",
        "selected_option_id",
    }
)


class ProjectedBusinessItem(dict[str, Any]):
    """Dictionary payload with non-serializable lineage context."""

    __slots__ = ("_eligible_parent_ids",)

    def __init__(
        self,
        item: Mapping[str, Any],
        *,
        eligible_parent_ids: set[str] | None = None,
    ) -> None:
        super().__init__(item)
        self._eligible_parent_ids = (
            frozenset(eligible_parent_ids) if eligible_parent_ids is not None else None
        )


def business_parent_context(item: Mapping[str, Any]) -> set[str] | None:
    context = getattr(item, "_eligible_parent_ids", None)
    return set(context) if context is not None else None


def project_business_item(
    item: Mapping[str, Any],
    *,
    eligible_parent_ids: set[str] | None = None,
) -> ProjectedBusinessItem:
    context = (
        eligible_parent_ids
        if eligible_parent_ids is not None
        else business_parent_context(item)
    )
    return ProjectedBusinessItem(item, eligible_parent_ids=context)


def evolve_business_item(
    item: Mapping[str, Any],
    **updates: Any,
) -> ProjectedBusinessItem:
    return project_business_item(
        {**item, **updates},
        eligible_parent_ids=business_parent_context(item),
    )


def _business_mask(items: list[Mapping[str, Any]]) -> set[int]:
    eligible_indexes: set[int] = set()
    eligible_ids: set[str] = set()
    changed = True
    while changed:
        changed = False
        for index, item in enumerate(items):
            if index in eligible_indexes:
                continue
            if not classify_business_item(
                item, eligible_parent_ids=eligible_ids
            ).eligible:
                continue
            eligible_indexes.add(index)
            item_id = str(item.get("id") or item.get("item_id") or "").strip()
            if item_id:
                eligible_ids.add(item_id)
            changed = True
    return eligible_indexes


def strip_business_fields(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in item.items() if key not in BUSINESS_ONLY_FIELDS
    }


def filter_business_items(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = list(items)
    eligible = _business_mask(rows)
    return [dict(item) for index, item in enumerate(rows) if index in eligible]


def diagnostic_items(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = list(items)
    eligible = _business_mask(rows)
    return [
        strip_business_fields(item)
        for index, item in enumerate(rows)
        if index not in eligible
    ]


def eligible_item_ids(items: Iterable[Mapping[str, Any]]) -> set[str]:
    return {
        str(item.get("id") or item.get("item_id"))
        for item in filter_business_items(items)
        if str(item.get("id") or item.get("item_id") or "").strip()
    }


def filter_by_eligible_parent(
    rows: Iterable[Mapping[str, Any]],
    eligible_ids: set[str],
    *,
    parent_key: str = "item_id",
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in rows
        if str(row.get(parent_key) or "").strip() in eligible_ids
    ]


def normalize_persisted_business_item(row: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(row)
    metadata = item.get("metadata")
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = {}
        metadata = parsed if isinstance(parsed, dict) else {}
    elif not isinstance(metadata, Mapping):
        metadata = {}
    item["metadata"] = dict(metadata)
    item["id"] = str(item.get("item_id") or "").strip()
    item["kind"] = str(item.get("item_kind") or "").strip()
    return item


def lineage_parent_ids(items: Iterable[Mapping[str, Any]]) -> set[str]:
    parent_ids: set[str] = set()
    for raw in items:
        item = normalize_persisted_business_item(raw)
        metadata = item["metadata"]
        lineage = metadata.get("lineage")
        lineage = lineage if isinstance(lineage, Mapping) else {}
        parent_id = str(
            metadata.get("parent_item_id")
            or metadata.get("source_item_id")
            or lineage.get("parent_item_id")
            or ""
        ).strip()
        if parent_id:
            parent_ids.add(parent_id)
    return parent_ids


def filter_business_decisions(
    decisions: Iterable[Mapping[str, Any]],
    linked_items: Iterable[Mapping[str, Any]],
    *,
    lineage_items: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    decision_rows = [dict(row) for row in decisions]
    linked = [
        normalize_persisted_business_item(row)
        for row in linked_items
        if str(row.get("item_id") or "").strip()
    ]
    lineage = [
        normalize_persisted_business_item(row)
        for row in lineage_items
        if str(row.get("item_id") or "").strip()
    ]
    eligible_ids = eligible_item_ids([*lineage, *linked])
    linked_decision_ids = {
        row.get("decision_id") for row in linked if row.get("decision_id") is not None
    }
    eligible_decision_ids = {
        row.get("decision_id")
        for row in linked
        if row.get("id") in eligible_ids and row.get("decision_id") is not None
    }
    return [
        row
        for row in decision_rows
        if row.get("id") not in linked_decision_ids
        or row.get("id") in eligible_decision_ids
    ]
