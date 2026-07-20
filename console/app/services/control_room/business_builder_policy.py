from __future__ import annotations

from collections.abc import Mapping, Set
from functools import wraps
from typing import Any

from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_projection import (
    business_parent_context,
    project_business_item,
    strip_business_fields,
)


def projection_context(
    item: Mapping[str, Any],
    eligible_parent_ids: Set[str] | None = None,
) -> set[str] | None:
    if eligible_parent_ids is not None:
        return {str(value) for value in eligible_parent_ids}
    return business_parent_context(item)


def business_builder_allowed(
    item: Mapping[str, Any],
    *,
    eligible_parent_ids: Set[str] | None = None,
) -> bool:
    context = projection_context(item, eligible_parent_ids)
    return classify_business_item(item, eligible_parent_ids=context).eligible


def diagnostic_projection(item: Mapping[str, Any]) -> dict[str, Any]:
    return strip_business_fields(item)


def blocked_priority_payload(
    item: Mapping[str, Any],
    *,
    eligible_parent_ids: Set[str] | None = None,
) -> dict[str, Any] | None:
    if business_builder_allowed(item, eligible_parent_ids=eligible_parent_ids):
        return None
    return _diagnostic_priority_payload()


def _diagnostic_priority_payload() -> dict[str, Any]:
    return {
        "score": 0,
        "band": "diagnostic",
        "drivers": [],
        "formula": "not_business_eligible",
    }


def blocked_impact_payload(
    item: Mapping[str, Any],
    *,
    eligible_parent_ids: Set[str] | None = None,
) -> dict[str, Any] | None:
    if business_builder_allowed(item, eligible_parent_ids=eligible_parent_ids):
        return None
    return _diagnostic_impact_payload(item)


def _diagnostic_impact_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "item_id": item.get("id"),
        "status": "not_business_eligible",
        "estimate": None,
        "currency": None,
        "confidence": 0.0,
        "priority_score": 0,
        "drivers": [],
        "formula": "not_business_eligible",
        "explanation": "Diagnostic items do not receive business impact estimates.",
    }


def _guard_business_builder(blocked_result):
    def decorator(builder):
        @wraps(builder)
        def guarded(item, *args, eligible_parent_ids=None, **kwargs):
            context = projection_context(item, eligible_parent_ids)
            if not business_builder_allowed(item, eligible_parent_ids=context):
                return blocked_result(item)
            projected = project_business_item(item, eligible_parent_ids=context)
            return builder(projected, *args, **kwargs)

        return guarded

    return decorator


business_priority_builder = _guard_business_builder(
    lambda _item: _diagnostic_priority_payload()
)
business_impact_builder = _guard_business_builder(_diagnostic_impact_payload)
business_template_ids_builder = _guard_business_builder(lambda _item: [])
business_action_templates_builder = _guard_business_builder(lambda _item: [])
business_primary_template_builder = _guard_business_builder(lambda _item: {})


__all__ = (
    "blocked_impact_payload",
    "blocked_priority_payload",
    "business_action_templates_builder",
    "business_builder_allowed",
    "business_impact_builder",
    "business_primary_template_builder",
    "business_priority_builder",
    "business_template_ids_builder",
    "diagnostic_projection",
    "projection_context",
)
