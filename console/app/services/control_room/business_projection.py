from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_lineage import (
    MAX_LINEAGE_DEPTH,
    item_identity,
    parent_references,
)


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
    identities = [item_identity(item) for item in items]
    counts = Counter(item_id for item_id in identities if item_id)
    by_id = {
        item_id: item
        for item_id, item in zip(identities, items, strict=True)
        if item_id
    }
    duplicate_ids = {item_id for item_id, count in counts.items() if count > 1}
    eligible_by_id: dict[str, bool] = {}
    visiting: set[str] = set()

    def _eligible(item: Mapping[str, Any], depth: int) -> bool:
        item_id = item_identity(item)
        if item_id and item_id in eligible_by_id:
            return eligible_by_id[item_id]
        if depth > MAX_LINEAGE_DEPTH or item_id in duplicate_ids:
            return False
        if item_id and item_id in visiting:
            return False
        if item_id:
            visiting.add(item_id)
        refs = parent_references(item)
        validated_context = business_parent_context(item) or set()
        parent_ids: set[str] = set()
        parents_valid = not refs.malformed
        for parent_id in refs.ids:
            parent = by_id.get(parent_id)
            if parent is None:
                if parent_id not in validated_context:
                    parents_valid = False
                    break
            elif not _eligible(parent, depth + 1):
                parents_valid = False
                break
            parent_ids.add(parent_id)
        result = (
            parents_valid
            and classify_business_item(
                item,
                eligible_parent_ids=parent_ids if refs.ids else set(),
            ).eligible
        )
        if item_id:
            visiting.discard(item_id)
            eligible_by_id[item_id] = result
        return result

    return {index for index, item in enumerate(items) if _eligible(item, 0)}


def strip_business_fields(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in item.items() if key not in BUSINESS_ONLY_FIELDS
    }


def filter_business_items(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = list(items)
    eligible = _business_mask(rows)
    eligible_ids = {
        item_id
        for index, item_id in enumerate(item_identity(item) for item in rows)
        if index in eligible and item_id
    }
    projected: list[dict[str, Any]] = []
    for index, item in enumerate(rows):
        if index not in eligible:
            continue
        context = business_parent_context(item) or set()
        context.update(parent_references(item).ids & eligible_ids)
        projected.append(project_business_item(item, eligible_parent_ids=context))
    return projected


def duplicate_item_ids(items: Iterable[Mapping[str, Any]]) -> set[str]:
    counts = Counter(item_id for item in items if (item_id := item_identity(item)))
    return {item_id for item_id, count in counts.items() if count > 1}


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
        parent_ids.update(parent_references(item).ids)
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
    linked_ids = {str(row.get("id") or "") for row in linked}
    eligible_ids = eligible_item_ids(
        [item for item in lineage if str(item.get("id") or "") not in linked_ids]
        + linked
    )
    linked_by_decision: dict[Any, list[Mapping[str, Any]]] = {}
    for row in linked:
        decision_id = row.get("decision_id")
        if decision_id is not None:
            linked_by_decision.setdefault(decision_id, []).append(row)
    linked_decision_ids = set(linked_by_decision)
    eligible_decision_ids = {
        decision_id
        for decision_id, rows in linked_by_decision.items()
        if rows and all(row.get("id") in eligible_ids for row in rows)
    }

    def _origin(row: Mapping[str, Any]) -> str:
        if row.get("_control_room_origin") is True:
            return "control_room"
        kpis = row.get("kpis")
        if isinstance(kpis, str):
            try:
                kpis = json.loads(kpis)
            except (TypeError, ValueError, json.JSONDecodeError):
                kpis = []
        if not isinstance(kpis, list):
            return ""
        first_origin = ""
        for entry in kpis:
            if not isinstance(entry, Mapping):
                continue
            legacy_source = str(entry.get("source") or "").strip().lower()
            if legacy_source == "control_room":
                return legacy_source
            provenance = entry.get("provenance")
            if isinstance(provenance, Mapping):
                origin = str(provenance.get("origin") or "").strip().lower()
                if origin == "control_room":
                    return origin
                if origin and not first_origin:
                    first_origin = origin
        return first_origin

    visible = []
    for row in decision_rows:
        decision_id = row.get("id")
        if decision_id in eligible_decision_ids:
            visible.append(row)
            continue
        if decision_id in linked_decision_ids:
            continue
        if _origin(row) != "control_room":
            visible.append(row)
    return visible
