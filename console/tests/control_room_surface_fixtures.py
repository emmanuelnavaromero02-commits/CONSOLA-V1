from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.control_room_surfaces import SurfaceScope
from app.services.control_room.surface_snapshot import SurfaceSnapshot
from app.services.control_room.business_explicit_action_binding import (
    attach_explicit_action_binding,
)
from control_room_runtime_evidence_fixture import bind_runtime_row_evidence


TENANT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
WORKSPACE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
GENERATED_AT = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)
VIEWER = {
    "id": 7,
    "role": "viewer",
    "active_tenant_id": TENANT_ID,
    "active_workspace_id": WORKSPACE_ID,
    "allowed_cartridges": ["sap_hcm"],
}
OPERATOR = {
    "id": 9,
    "role": "tenant_admin",
    "active_tenant_id": TENANT_ID,
    "active_workspace_id": WORKSPACE_ID,
    "allowed_cartridges": ["sap_hcm"],
}


def business_item(item_id: str = "business-1", **updates: object) -> dict[str, object]:
    item: dict[str, object] = {
        "id": item_id,
        "kind": "anomaly",
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "source_dataset": "gold_business_observations",
        "source_system": "sap_hcm",
        "cartridge": "sap_hcm",
        "entity_kind": "employee",
        "entity_id": "employee-1001",
        "entity_label": "Observed employee",
        "title": "Observed business condition",
        "module_id": "people_overview",
        "module": "People",
        "domain": "People",
        "severity": "high",
        "detected_at": "2026-07-20T10:00:00Z",
        "data_status": "ready",
        "status": "open",
        "execution_status": "not_started",
        "metric_type": "count",
        "observed_value": 2,
        "population_count": 10,
    }
    item.update(updates)
    if item.get("observed_value") is None:
        item.pop("observed_value")
    return bind_runtime_row_evidence(
        item,
        locator_field="entity_id",
        observed_at=str(item["detected_at"]),
    )


def action_item(
    item_id: str = "business-1",
    *,
    template_id: str = "request_owner_review",
    **updates: object,
) -> dict[str, object]:
    return attach_explicit_action_binding(
        business_item(item_id, **updates),
        template_id=template_id,
    )


def source_state(
    item_id: str = "source-state-1", **updates: object
) -> dict[str, object]:
    item: dict[str, object] = {
        "id": item_id,
        "kind": "source_state",
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "title": "Source unavailable",
        "module_id": "people_overview",
        "module": "People",
        "domain": "People",
        "cartridge": "sap_hcm",
        "source_dataset": "gold_business_observations",
        "status": "open",
        "data_status": "missing",
        "checked_at": "2026-07-20T10:00:00Z",
    }
    item.update(updates)
    return item


def source_status(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "cartridge": "sap_hcm",
        "dataset": "gold_business_observations",
        "module": "People",
        "domain": "People",
        "status": "missing",
        "data_readiness": "missing",
        "count": 0,
        "operationally_ready": False,
        "checked_at": "2026-07-20T10:00:00Z",
        "readiness_reason": "Materialization pending",
        "readiness_blockers": ["missing"],
        "contract_warnings": [],
    }
    row.update(updates)
    return row


def installation(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "cartridge_id": "sap_hcm",
        "installation_status": "ready",
        "current_step": "test_registry",
        "label": "SAP HCM",
        "category": "hr",
        "ready_at": "2026-07-20T09:00:00Z",
    }
    row.update(updates)
    return row


def snapshot(
    *,
    items: tuple[dict[str, object], ...] = (),
    diagnostics: tuple[dict[str, object], ...] = (),
    sources: tuple[dict[str, object], ...] = (),
    installations: tuple[dict[str, object], ...] = (),
) -> SurfaceSnapshot:
    return SurfaceSnapshot(
        generated_at=GENERATED_AT,
        scope=SurfaceScope(tenant_id=TENANT_ID, workspace_id=WORKSPACE_ID),
        items=items,
        diagnostics=diagnostics,
        sources=sources,
        installations=installations,
    )


__all__ = (
    "GENERATED_AT",
    "OPERATOR",
    "TENANT_ID",
    "VIEWER",
    "WORKSPACE_ID",
    "action_item",
    "business_item",
    "installation",
    "snapshot",
    "source_state",
    "source_status",
)
