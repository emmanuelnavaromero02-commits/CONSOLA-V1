from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.control_room.business_command_item import resolve_command_item
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    WorkflowStage,
    workflow_eligibility_provenance,
)


USER = {
    "id": 7,
    "role": "workspace_admin",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}


def _item(value: int) -> dict:
    item = {
        "id": "item-1",
        "item_id": "item-1",
        "kind": "intelligence_signal",
        "item_kind": "intelligence_signal",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "owner_user_id": 7,
        "cartridge": "sap_hcm",
        "source_system": "sap_hcm",
        "source_dataset": "gold_people",
        "title": "Measured anomaly",
        "description": "Measured anomaly",
        "recommendation": "Review",
        "entity_label": "Employee",
        "severity": "high",
        "observed_value": value,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-22",
    }
    return {
        **item,
        **runtime_row_evidence_fields(
            source_dataset="gold_people",
            source_system="sap_hcm",
            cartridge="sap_hcm",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            source_row={"item_id": item["id"], "observed_value": value},
            locator_field="item_id",
            observed_at=item["observation_date"],
        ),
    }


def _persisted_a(*, linked: bool = True) -> dict:
    item = _item(1)
    if not linked:
        provenance = workflow_eligibility_provenance(
            item,
            stage=WorkflowStage.OPTION_SELECTED,
            workspace_id="workspace-a",
            option_id="review",
        )
        return {
            **item,
            "decision_id": None,
            "selected_option_id": None,
            "execution_status": "not_started",
            "status": "open",
            "metadata": {
                CURRENT_ELIGIBILITY_FINGERPRINT_KEY: provenance["fingerprint"]
            },
        }
    provenance = workflow_eligibility_provenance(
        item,
        stage=WorkflowStage.DECISION_CREATED,
        workspace_id="workspace-a",
        decision_id=42,
    )
    return {
        **item,
        "decision_id": 42,
        "selected_option_id": "review",
        "execution_status": "not_started",
        "status": "decision_created",
        "metadata": {
            CURRENT_ELIGIBILITY_FINGERPRINT_KEY: provenance["fingerprint"],
            DECISION_PROVENANCE_KEY: provenance,
        },
    }


async def _resolve(persisted: dict, collect_items: AsyncMock) -> dict:
    return await resolve_command_item(
        "item-1",
        USER,
        load_persisted=AsyncMock(return_value=persisted),
        collect_items=collect_items,
        normalize_lineage=dict,
        pool_factory=AsyncMock(),
        run_scoped=AsyncMock(),
        projector=lambda item, **_kwargs: item,
    )


@pytest.mark.asyncio
async def test_linked_persisted_a_cannot_hide_or_operate_live_b() -> None:
    collect = AsyncMock(return_value={"items": [_item(2)], "diagnostics": []})

    with pytest.raises(HTTPException) as exc:
        await _resolve(_persisted_a(), collect)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "item_workflow_quarantined"
    collect.assert_awaited_once()


@pytest.mark.asyncio
async def test_unlinked_persisted_a_allows_explicit_live_b_flow() -> None:
    collect = AsyncMock(return_value={"items": [_item(2)], "diagnostics": []})

    item = await _resolve(_persisted_a(linked=False), collect)

    assert item["observed_value"] == 2
    assert item.get("decision_id") is None
    collect.assert_awaited_once()


@pytest.mark.asyncio
async def test_wrong_scope_is_404_before_live_generation_lookup() -> None:
    persisted = {**_persisted_a(), "tenant_id": "tenant-b"}
    collect = AsyncMock(return_value={"items": [_item(2)], "diagnostics": []})

    with pytest.raises(HTTPException) as exc:
        await _resolve(persisted, collect)

    assert exc.value.status_code == 404
    collect.assert_not_awaited()
