from __future__ import annotations

from collections.abc import Mapping

import pytest

from app.services.control_room.business_orchestrator import (
    eligible_orchestrator_source,
)
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_state_overlay import (
    load_overlay_state,
    overlay_business_state,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    ELIGIBILITY_POLICY_VERSION,
    ELIGIBILITY_POLICY_VERSION_KEY,
    WORKFLOW_QUARANTINE_KEY,
    WorkflowStage,
    business_observation_fingerprint,
    workflow_eligibility_provenance,
)


def _source_row(item: Mapping) -> dict:
    fields = (
        "id",
        "kind",
        "metric_name",
        "metric_type",
        "observed_value",
        "observation_date",
        "tenant_id",
        "workspace_id",
    )
    return {field: item[field] for field in fields}


def _item(**overrides) -> dict:
    item = {
        "id": "business-1",
        "kind": "anomaly",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "cartridge": "sap_hcm",
        "source_dataset": "gold_metrics",
        "source_system": "sap_hcm",
        "metric_name": "measured_metric",
        "metric_type": "scalar",
        "observed_value": 2,
        "observation_date": "2026-07-20",
        **overrides,
    }
    return {
        **item,
        **runtime_row_evidence_fields(
            source_dataset=item["source_dataset"],
            source_system=item["source_system"],
            cartridge=item["cartridge"],
            tenant_id=item["tenant_id"],
            workspace_id=item["workspace_id"],
            source_row=_source_row(item),
            locator_field="id",
            observed_at=item["observation_date"],
        ),
    }


def _state(item: Mapping, *, provenance_workspace: str) -> dict:
    provenance_item = _item(workspace_id=provenance_workspace)
    metadata = {
        **item,
        "intelligence": {"options": [{"id": "foreign"}]},
        CURRENT_ELIGIBILITY_FINGERPRINT_KEY: business_observation_fingerprint(item),
        ELIGIBILITY_POLICY_VERSION_KEY: ELIGIBILITY_POLICY_VERSION,
        DECISION_PROVENANCE_KEY: workflow_eligibility_provenance(
            provenance_item,
            stage=WorkflowStage.DECISION_CREATED,
            workspace_id=provenance_workspace,
            decision_id=42,
        ),
    }
    return {
        "item_id": item["id"],
        "item_kind": item["kind"],
        "source_dataset": item["source_dataset"],
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "owner_user_id": 7,
        "status": "decision_created",
        "decision_id": 42,
        "execution_status": "not_started",
        "impact_estimate": 999,
        "metadata": metadata,
    }


def _overlay(item: dict, state: dict) -> dict:
    return overlay_business_state(
        [item],
        {item["id"]: state},
        item_statuses={"open", "decision_created"},
        projector=lambda value, **_kwargs: value,
        sort_key=lambda value: value["id"],
    )[0]


def _assert_workflow_and_artifacts_cleared(projected: Mapping) -> None:
    assert projected["status"] == "open"
    assert projected["decision_id"] is None
    assert projected.get("impact_estimate") is None
    assert projected["intelligence"] == {}


def test_foreign_workspace_provenance_cannot_overlay_workflow_or_artifacts() -> None:
    item = _item()

    projected = _overlay(item, _state(item, provenance_workspace="workspace-b"))

    _assert_workflow_and_artifacts_cleared(projected)


def test_quarantined_state_cannot_overlay_workflow_or_artifacts() -> None:
    item = _item()
    state = _state(item, provenance_workspace="workspace-a")
    state["metadata"][WORKFLOW_QUARANTINE_KEY] = {"reason": "legacy"}

    _assert_workflow_and_artifacts_cleared(_overlay(item, state))


class OverlayLoaderConnection:
    def __init__(self) -> None:
        self.sql = ""

    async def fetch(self, sql: str, *_args):
        self.sql = " ".join(sql.split()).lower()
        return []


@pytest.mark.asyncio
async def test_overlay_loader_selects_scope_identity() -> None:
    conn = OverlayLoaderConnection()

    await load_overlay_state(
        conn,
        workspace_id="workspace-a",
        item_ids=["business-1"],
        tenant_id="tenant-a",
        owner_id=7,
    )

    projection = conn.sql.split("from control_room_items", 1)[0]
    assert "tenant_id" in projection
    assert "workspace_id" in projection


class NoLineageRead:
    async def fetch(self, *_args, **_kwargs):
        raise AssertionError("quarantined source must fail before lineage lookup")


@pytest.mark.asyncio
async def test_quarantined_item_is_not_an_orchestrator_source() -> None:
    item = _item()
    item["metadata"] = {WORKFLOW_QUARANTINE_KEY: {"reason": "legacy"}}

    result = await eligible_orchestrator_source(
        NoLineageRead(),
        item,
        source_type="anomaly",
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        owner_id=7,
    )

    assert result is None
