from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
from heapq import heappop, heappush
from typing import Any

from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_lineage import (
    MAX_LINEAGE_DEPTH,
    ParentReferences,
    item_identity,
    parent_references,
)


@dataclass(frozen=True)
class BusinessLineageResolution:
    eligible: bool
    depth: int | None
    cause: str


def _parent_context(item: Mapping[str, Any]) -> frozenset[str]:
    raw = getattr(item, "_eligible_parent_ids", None)
    if not isinstance(raw, Set):
        return frozenset()
    return frozenset(clean for value in raw if (clean := str(value or "").strip()))


def _preserved_depth(item: Mapping[str, Any]) -> int | None:
    depth = getattr(item, "_lineage_depth", None)
    if isinstance(depth, int) and not isinstance(depth, bool) and depth >= 0:
        return depth
    return None


def _reference_blocked(
    item: Mapping[str, Any],
    refs: ParentReferences,
    known_ids: set[str],
    duplicate_ids: set[str],
) -> bool:
    if refs.malformed or refs.ids & duplicate_ids:
        return True
    return bool(refs.ids - known_ids - _parent_context(item))


def _lineage_depth(
    item: Mapping[str, Any],
    refs: ParentReferences,
    parent_results: Mapping[str, BusinessLineageResolution],
) -> int | None:
    if not refs.ids:
        depth = 0
    else:
        parent_depths = [
            parent_results[parent_id].depth if parent_id in parent_results else 0
            for parent_id in refs.ids
        ]
        if any(parent_depth is None for parent_depth in parent_depths):
            return None
        depth = max(parent_depths, default=-1) + 1
    preserved = _preserved_depth(item)
    return max(depth, preserved) if preserved is not None else depth


def _evaluate(
    item: Mapping[str, Any],
    refs: ParentReferences,
    parent_results: Mapping[str, BusinessLineageResolution],
    known_ids: set[str],
    duplicate_ids: set[str],
) -> BusinessLineageResolution:
    if refs.malformed:
        return BusinessLineageResolution(False, None, "invalid_lineage")
    if refs.ids & duplicate_ids:
        return BusinessLineageResolution(False, None, "duplicate_parent")

    missing_ids = refs.ids - known_ids - _parent_context(item)
    if missing_ids:
        return BusinessLineageResolution(False, None, "missing_parent")

    depth = _lineage_depth(item, refs, parent_results)
    for parent_id in sorted(refs.ids):
        parent = parent_results.get(parent_id)
        if parent is not None and not parent.eligible:
            return BusinessLineageResolution(False, depth, parent.cause)
    if depth is None:
        return BusinessLineageResolution(False, None, "unresolved_parent")
    if depth > MAX_LINEAGE_DEPTH:
        return BusinessLineageResolution(False, depth, "maximum_depth")

    eligibility = classify_business_item(
        item,
        eligible_parent_ids=set(refs.ids),
    )
    return BusinessLineageResolution(
        eligibility.eligible,
        depth,
        eligibility.reason.value,
    )


def resolve_business_lineage(
    items: Iterable[Mapping[str, Any]],
) -> tuple[BusinessLineageResolution, ...]:
    """Resolve eligibility from the complete graph, independent of row order."""
    rows = tuple(items)
    identities = tuple(item_identity(item) for item in rows)
    counts = Counter(item_id for item_id in identities if item_id)
    duplicate_ids = {item_id for item_id, count in counts.items() if count > 1}
    unique_items = {
        item_id: item
        for item_id, item in zip(identities, rows, strict=True)
        if item_id and item_id not in duplicate_ids
    }
    known_ids = set(unique_items)
    refs_by_id = {
        item_id: parent_references(item) for item_id, item in unique_items.items()
    }

    dependencies: dict[str, set[str]] = {}
    children: dict[str, set[str]] = {item_id: set() for item_id in known_ids}
    for item_id, item in unique_items.items():
        refs = refs_by_id[item_id]
        dependencies[item_id] = (
            set()
            if _reference_blocked(item, refs, known_ids, duplicate_ids)
            else set(refs.ids & known_ids)
        )
        for parent_id in dependencies[item_id]:
            children[parent_id].add(item_id)

    pending = [item_id for item_id, parents in dependencies.items() if not parents]
    pending.sort()
    results_by_id: dict[str, BusinessLineageResolution] = {}
    while pending:
        item_id = heappop(pending)
        item = unique_items[item_id]
        results_by_id[item_id] = _evaluate(
            item,
            refs_by_id[item_id],
            results_by_id,
            known_ids,
            duplicate_ids,
        )
        for child_id in sorted(children[item_id]):
            dependencies[child_id].discard(item_id)
            if not dependencies[child_id]:
                heappush(pending, child_id)

    for item_id in sorted(known_ids - results_by_id.keys()):
        results_by_id[item_id] = BusinessLineageResolution(False, None, "cycle")

    resolved: list[BusinessLineageResolution] = []
    for item, item_id in zip(rows, identities, strict=True):
        if item_id in duplicate_ids:
            resolved.append(BusinessLineageResolution(False, None, "duplicate_id"))
        elif item_id:
            resolved.append(results_by_id[item_id])
        else:
            resolved.append(
                _evaluate(
                    item,
                    parent_references(item),
                    results_by_id,
                    known_ids,
                    duplicate_ids,
                )
            )
    return tuple(resolved)


__all__ = ("BusinessLineageResolution", "resolve_business_lineage")
