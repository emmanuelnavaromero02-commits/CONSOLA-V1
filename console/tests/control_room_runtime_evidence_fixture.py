from __future__ import annotations

from collections.abc import Mapping

from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)


def bind_runtime_row_evidence(
    item: dict,
    *,
    locator_field: str,
    observed_at: str,
    source_row: Mapping | None = None,
) -> dict:
    row = dict(source_row or item)
    row.setdefault(
        locator_field,
        item.get(locator_field) or item.get("item_id") or item["id"],
    )
    item.update(
        runtime_row_evidence_fields(
            source_dataset=item["source_dataset"],
            source_system=item["source_system"],
            cartridge=item["cartridge"],
            tenant_id=item["tenant_id"],
            workspace_id=item["workspace_id"],
            source_row=row,
            locator_field=locator_field,
            observed_at=observed_at,
            business_observation=item,
        )
    )
    return item
