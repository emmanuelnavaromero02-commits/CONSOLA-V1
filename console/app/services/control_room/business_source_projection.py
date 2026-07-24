from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.services.control_room.business_source_scope import (
    scoped_runtime_evidence_fields,
    scoped_source_row,
)


_OBSERVED_AT_FIELDS = (
    "detected_at",
    "generated_at",
    "observation_date",
    "freshness_at",
    "period_key",
    "as_of",
    "mes",
    "semana",
    "spend_month",
)


def _first_value(row: Mapping[str, Any], fields: Sequence[str]) -> tuple[str, str]:
    for field in fields:
        value = str(row.get(field) or "").strip()
        if value:
            return field, value
    return "", ""


def append_normalized_business_rows(
    items: list[dict[str, Any]],
    source: Any,
    rows: Sequence[Mapping[str, Any]],
    thresholds: Mapping[str, Any],
    user: Mapping[str, Any] | None,
    *,
    workspace_scope: Callable[[Mapping[str, Any] | None], tuple[str, str]],
    normalize: Callable[[Any, dict[str, Any], Mapping[str, Any]], dict | None],
) -> None:
    tenant_id, workspace_id = workspace_scope(user)
    locator_fields = (source.entity_id_field, source.entity_label_field)
    for row in rows:
        scoped = scoped_source_row(
            row,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        item = normalize(source, scoped, thresholds)
        if not item:
            continue
        locator_field, _locator = _first_value(scoped, locator_fields)
        _observed_field, observed_at = _first_value(scoped, _OBSERVED_AT_FIELDS)
        if locator_field and observed_at:
            item.update(
                scoped_runtime_evidence_fields(
                    scoped,
                    source_dataset=source.dataset,
                    source_system=source.cartridge,
                    cartridge=source.cartridge,
                    locator_field=locator_field,
                    observed_at=observed_at,
                    business_observation=item,
                )
            )
        items.append(item)


__all__ = ("append_normalized_business_rows",)
