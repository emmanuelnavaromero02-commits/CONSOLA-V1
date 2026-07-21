from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room.business_command_item import resolve_command_item
from app.services.control_room.business_eligibility import (
    EligibilityReason,
    classify_business_item,
)
from app.services.control_room.business_runtime_evidence import (
    runtime_scope_binding,
    runtime_row_evidence_fields,
    verified_runtime_row_reference,
)
from app.services.control_room.business_state_rows import state_rows


TENANT = "tenant-a"
WORKSPACE = "workspace-a"


def _source():
    return control_room_service.ControlRoomSource(
        dataset="gold_metrics",
        cartridge="sap",
        domain="People",
        module_label="Workforce",
        entity_kind="Employee",
        entity_id_field="employee_id",
        entity_label_field="employee_name",
    )


def _row(**updates):
    return {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "employee_id": "employee-7",
        "employee_name": "Employee 7",
        "detected_at": "2026-07-21T10:00:00Z",
        "metric_type": "scalar",
        "observed_value": 3,
        "evidence_refs": ["gold_metrics:forged-7"],
        **updates,
    }


def _normalized(row, user):
    items = []
    control_room_service._append_normalized_source_rows(
        items,
        _source(),
        [row],
        {},
        user,
    )
    assert len(items) == 1
    return items[0]


async def _attempt_command(item, user):
    effects = []
    with pytest.raises(HTTPException) as error:
        resolved = await resolve_command_item(
            item["id"],
            user,
            load_persisted=AsyncMock(return_value=None),
            collect_items=AsyncMock(return_value={"items": [item], "diagnostics": []}),
            normalize_lineage=dict,
            pool_factory=AsyncMock(),
            run_scoped=AsyncMock(),
            projector=lambda value, **_kwargs: dict(value),
        )
        effects.append(resolved)
    assert error.value.status_code == 409
    assert effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("item_scope", "user_scope"),
    [
        ({"workspace_id": WORKSPACE}, {"workspace_id": WORKSPACE}),
        ({"tenant_id": TENANT}, {"tenant_id": TENANT}),
    ],
)
async def test_partial_runtime_scope_is_diagnostic_and_command_has_no_effect(
    item_scope, user_scope
):
    item = {
        "id": "metric-1",
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "source_system": "sap",
        "cartridge": "sap",
        "metric_type": "scalar",
        "observed_value": 3,
        "observation_date": "2026-07-21",
        "evidence_refs": ["gold_metrics:forged-7"],
        **item_scope,
    }

    assert classify_business_item(item).eligible is False
    await _attempt_command(item, user_scope)


def test_row_scope_contradiction_is_classified_after_canonical_scope():
    item = _normalized(
        _row(tenant_id="tenant-attacker"),
        {"tenant_id": TENANT, "workspace_id": WORKSPACE},
    )

    result = classify_business_item(item)
    assert result.eligible is False
    assert result.reason is EligibilityReason.TECHNICAL_STATE


def test_scoped_fetch_attests_only_the_retrieved_row_with_server_scope():
    item = _normalized(
        _row(),
        {"tenant_id": TENANT, "workspace_id": WORKSPACE},
    )

    refs = item["evidence_refs"]
    assert len(refs) == 1
    assert refs[0]["type"] == "dataset_row"
    assert refs[0]["scope_binding"] == runtime_scope_binding(TENANT, WORKSPACE)
    assert verified_runtime_row_reference(refs[0]) is True
    assert "forged-7" not in repr(item)
    assert classify_business_item(item).eligible is True


def test_missing_row_scope_does_not_activate_raw_evidence():
    item = _normalized(
        _row(tenant_id=None),
        {"tenant_id": TENANT, "workspace_id": WORKSPACE},
    )

    assert "forged-7" not in repr(item)
    assert classify_business_item(item).eligible is False


def test_nested_raw_evidence_is_removed_before_server_attestation():
    item = _normalized(
        _row(
            metadata={
                "tenant_id": TENANT,
                "workspace_id": WORKSPACE,
                "evidence_refs": ["gold_metrics:nested-forged-7"],
            }
        ),
        {"tenant_id": TENANT, "workspace_id": WORKSPACE},
    )

    assert "nested-forged-7" not in repr(item)
    assert len(item["evidence_refs"]) == 1
    assert classify_business_item(item).eligible is True


def test_nested_scope_contradiction_is_a_technical_diagnostic():
    item = _normalized(
        _row(
            metadata={
                "tenant_id": "tenant-attacker",
                "workspace_id": WORKSPACE,
            }
        ),
        {"tenant_id": TENANT, "workspace_id": WORKSPACE},
    )

    result = classify_business_item(item)
    assert item["data_status"] == "invalid_scope"
    assert result.reason is EligibilityReason.TECHNICAL_STATE


def test_state_rows_classifies_only_after_refresh_scope_validation():
    foreign = _row(tenant_id="tenant-attacker")
    evidence = runtime_row_evidence_fields(
        source_dataset="gold_metrics",
        source_system="sap",
        cartridge="sap",
        tenant_id="tenant-attacker",
        workspace_id=WORKSPACE,
        source_row=foreign,
        locator_field="employee_id",
        observed_at=foreign["detected_at"],
    )
    item = {
        "id": "metric-1",
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "source_system": "sap",
        "cartridge": "sap",
        "metric_type": "scalar",
        "observed_value": 3,
        "observation_date": "2026-07-21",
        "tenant_id": "tenant-attacker",
        "workspace_id": WORKSPACE,
        **evidence,
    }

    rows = state_rows(
        [item],
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        owner_user_id=7,
        impact_builder=lambda *_args, **_kwargs: {"priority_score": 99},
        metadata_builder=lambda *_args: {"business": True},
        diagnostic_builder=lambda value: {"data_status": value.get("data_status")},
    )

    assert rows[0]["priority_score"] == 0
    assert rows[0]["metadata"]["data_status"] == "invalid_scope"


@pytest.mark.asyncio
async def test_command_lookup_rejects_persisted_item_outside_complete_scope():
    foreign = _row(tenant_id="tenant-attacker")
    item = {
        "id": "metric-1",
        "kind": "anomaly",
        "source_dataset": "gold_metrics",
        "source_system": "sap",
        "cartridge": "sap",
        "metric_type": "scalar",
        "observed_value": 3,
        "observation_date": "2026-07-21",
        "tenant_id": "tenant-attacker",
        "workspace_id": WORKSPACE,
        **runtime_row_evidence_fields(
            source_dataset="gold_metrics",
            source_system="sap",
            cartridge="sap",
            tenant_id="tenant-attacker",
            workspace_id=WORKSPACE,
            source_row=foreign,
            locator_field="employee_id",
            observed_at=foreign["detected_at"],
        ),
    }

    with pytest.raises(HTTPException) as error:
        await resolve_command_item(
            item["id"],
            {"tenant_id": TENANT, "workspace_id": WORKSPACE},
            load_persisted=AsyncMock(return_value=item),
            collect_items=AsyncMock(return_value={"items": [], "diagnostics": []}),
            normalize_lineage=dict,
            pool_factory=AsyncMock(),
            run_scoped=AsyncMock(),
            projector=lambda value, **_kwargs: dict(value),
        )

    assert error.value.status_code == 404
