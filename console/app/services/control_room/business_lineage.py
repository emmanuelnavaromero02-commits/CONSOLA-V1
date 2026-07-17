from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.services.control_room.business_observation import has_evidence, semantic_maps


MAX_LINEAGE_DEPTH = 16
DERIVED_KINDS = frozenset({"agent_alert", "derived", "intelligence_signal"})


@dataclass(frozen=True)
class ParentReferences:
    ids: frozenset[str]
    malformed: bool = False


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def item_identity(item: Mapping[str, Any]) -> str:
    for key in ("id", "item_id", "source_id"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def item_kinds(item: Mapping[str, Any]) -> frozenset[str]:
    kinds: set[str] = set()
    for values in semantic_maps(item):
        for key in ("kind", "item_kind"):
            value = str(values.get(key) or "").strip().lower()
            if value:
                kinds.add(value)
    return frozenset(kinds)


def is_source_state(item: Mapping[str, Any]) -> bool:
    return "source_state" in item_kinds(item)


def _reference_value(value: Any) -> tuple[set[str], bool]:
    if value is None:
        return set(), True
    if isinstance(value, str):
        clean = value.strip()
        return ({clean} if clean else set()), not bool(clean)
    if isinstance(value, Mapping):
        ids: set[str] = set()
        malformed = False
        found = False
        for key in ("item_id", "id"):
            if key not in value:
                continue
            found = True
            clean = str(value.get(key) or "").strip()
            if clean:
                ids.add(clean)
            else:
                malformed = True
        return ids, malformed or not found
    if isinstance(value, (list, tuple, set, frozenset)):
        ids: set[str] = set()
        malformed = False
        for entry in value:
            parsed, invalid = _reference_value(entry)
            ids.update(parsed)
            malformed = malformed or invalid
        return ids, malformed
    return set(), True


def parent_references(item: Mapping[str, Any]) -> ParentReferences:
    ids: set[str] = set()
    malformed = False
    for values in semantic_maps(item):
        for key in ("parent_item_id", "source_item_id", "derived_from"):
            if key not in values:
                continue
            parsed, invalid = _reference_value(values.get(key))
            ids.update(parsed)
            malformed = malformed or invalid
        if "lineage" not in values:
            continue
        lineage = values.get("lineage")
        if not isinstance(lineage, Mapping):
            malformed = True
            continue
        for key in ("parent_item_id", "source_item_id"):
            if key not in lineage:
                continue
            parsed, invalid = _reference_value(lineage.get(key))
            ids.update(parsed)
            malformed = malformed or invalid
    return ParentReferences(frozenset(ids), malformed)


def is_derived(item: Mapping[str, Any]) -> bool:
    refs = parent_references(item)
    return bool(item_kinds(item) & DERIVED_KINDS or refs.ids or refs.malformed)


def has_root_lineage(item: Mapping[str, Any]) -> bool:
    source = ""
    lineage_root = False
    for values in semantic_maps(item):
        source = (
            source
            or str(
                values.get("source_dataset")
                or values.get("dataset")
                or values.get("gold_table")
                or values.get("source_system")
                or ""
            ).strip()
        )
        lineage = values.get("lineage")
        if isinstance(lineage, Mapping):
            lineage_root = lineage_root or bool(
                lineage.get("source_dataset")
                or lineage.get("dataset")
                or lineage.get("root_source")
            )
    return bool((source or lineage_root) and has_evidence(item))
