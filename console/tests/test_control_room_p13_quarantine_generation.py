from __future__ import annotations

import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.control_room.business_decision_persistence import (
    create_and_link_decision,
)
from app.services.control_room.business_command_item import (
    resolve_command_item,
)
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
    WorkflowStage,
    business_observation_fingerprint,
    persistence_metadata,
    workflow_eligibility_provenance,
    workflow_is_quarantined,
)
from app.services.control_room.business_workflow_reconciliation import (
    workflow_metadata_patches,
)


def _item(value: int) -> dict:
    item = {
        "id": "business-1",
        "item_id": "business-1",
        "kind": "anomaly",
        "item_kind": "anomaly",
        "workspace_id": "workspace-a",
        "tenant_id": "tenant-a",
        "owner_user_id": 7,
        "cartridge": "sap_hcm",
        "source_system": "sap_hcm",
        "source_dataset": "gold_people",
        "title": "Measured anomaly",
        "entity_label": "Employee",
        "description": "Measured anomaly",
        "recommendation": "Review",
        "severity": "high",
        "observed_value": value,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-20",
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
            business_observation=item,
        ),
    }


def _workflow_a() -> dict:
    item = _item(1)
    provenance = workflow_eligibility_provenance(
        item,
        stage=WorkflowStage.DECISION_CREATED,
        workspace_id="workspace-a",
        decision_id=42,
    )
    return {
        **item,
        "decision_id": 42,
        "status": "decision_created",
        "selected_option_id": "review",
        "execution_status": "not_started",
        "decision_workspace_id": "workspace-a",
        "metadata": {
            CURRENT_ELIGIBILITY_FINGERPRINT_KEY: provenance["fingerprint"],
            DECISION_PROVENANCE_KEY: provenance,
        },
    }


class RefreshState:
    def __init__(self) -> None:
        self.row = _workflow_a()

    async def fetch(self, *_args):
        return [deepcopy(self.row)]

    async def refresh(self, item: dict) -> dict:
        prepared = {**item, "metadata": persistence_metadata(item)}
        patch = (await workflow_metadata_patches(self, [prepared]))[
            ("workspace-a", "business-1")
        ]
        metadata = {**prepared["metadata"], **patch}
        quarantined = (
            WORKFLOW_QUARANTINE_KEY in patch and DECISION_PROVENANCE_KEY not in patch
        )
        self.row = {
            **self.row,
            **item,
            "decision_id": None if quarantined else self.row.get("decision_id"),
            "status": "open" if quarantined else self.row.get("status"),
            "selected_option_id": (
                None if quarantined else self.row.get("selected_option_id")
            ),
            "execution_status": (
                "not_started" if quarantined else self.row.get("execution_status")
            ),
            "metadata": metadata,
        }
        return patch


@pytest.mark.asyncio
async def test_quarantine_is_bound_to_workflow_a_generation() -> None:
    state = RefreshState()
    patch = await state.refresh(_item(2))
    quarantine = patch[WORKFLOW_QUARANTINE_KEY]
    generations = quarantine["generations"]
    assert generations == [
        {
            "fingerprint": business_observation_fingerprint(_item(1)),
            "decision_id": 42,
            "stage": "decision_created",
            "reason": "observation_changed",
            "quarantined_at": generations[0]["quarantined_at"],
        }
    ]


@pytest.mark.asyncio
async def test_three_refreshes_leave_a_inert_and_b_actionable() -> None:
    state = RefreshState()
    observation_b = _item(2)
    for _ in range(3):
        await state.refresh(observation_b)
    assert state.row["decision_id"] is None
    assert state.row["selected_option_id"] is None
    assert state.row["execution_status"] == "not_started"
    assert len(state.row["metadata"][WORKFLOW_QUARANTINE_KEY]["generations"]) == 1
    assert not workflow_is_quarantined(state.row)
    resolved = await resolve_command_item(
        "business-1",
        {
            "id": 7,
            "role": "workspace_admin",
            "active_tenant_id": "tenant-a",
            "active_workspace_id": "workspace-a",
        },
        load_persisted=AsyncMock(return_value=state.row),
        collect_items=AsyncMock(
            return_value={"items": [observation_b], "diagnostics": []}
        ),
        normalize_lineage=dict,
        pool_factory=AsyncMock(),
        run_scoped=AsyncMock(),
        projector=lambda item, **_kwargs: item,
    )
    assert resolved["id"] == "business-1"


class DecisionConnection:
    def __init__(self, item: dict, *, selected_option_id: str | None = None) -> None:
        self.item = deepcopy(item)
        self.item["selected_option_id"] = selected_option_id
        self.inserted = 0

    async def fetchrow(self, sql: str, *args):
        normalized = " ".join(sql.split()).upper()
        if "FOR UPDATE" in normalized:
            return deepcopy(self.item)
        if normalized.startswith("INSERT INTO DECISIONS"):
            self.inserted += 1
            return {"id": 84, "title": "Workflow B"}
        if normalized.startswith("INSERT INTO DECISION_ACTIONS"):
            return {"id": 9}
        if normalized.startswith("UPDATE CONTROL_ROOM_ITEMS"):
            metadata = json.loads(args[3])
            self.item["metadata"] = {**self.item["metadata"], **metadata}
            self.item["decision_id"] = args[0]
            return {"item_id": "business-1"}
        raise AssertionError(normalized)


@pytest.mark.asyncio
async def test_workflow_b_requires_clean_columns_and_links_new_provenance() -> None:
    state = RefreshState()
    observation_b = _item(2)
    await state.refresh(observation_b)
    dirty = DecisionConnection(state.row, selected_option_id="legacy-option")
    with pytest.raises(HTTPException) as exc:
        await create_and_link_decision(
            dirty,
            user={"id": 7, "email": "owner@example.com"},
            item=observation_b,
            workspace_id="workspace-a",
            ensure_item_row=AsyncMock(),
            record_item_event=AsyncMock(),
        )
    assert exc.value.status_code == 409
    assert dirty.inserted == 0
    clean = DecisionConnection(state.row)
    decision = await create_and_link_decision(
        clean,
        user={"id": 7, "email": "owner@example.com"},
        item=observation_b,
        workspace_id="workspace-a",
        ensure_item_row=AsyncMock(),
        record_item_event=AsyncMock(),
    )
    provenance = clean.item["metadata"][DECISION_PROVENANCE_KEY]
    assert decision["id"] == clean.item["decision_id"] == 84
    assert provenance["fingerprint"] == business_observation_fingerprint(observation_b)
    assert provenance["decision_id"] == 84
    assert provenance["stage"] == "decision_created"
    assert provenance["reason"] == "explicit_decision_creation"
    assert provenance["linked_at"]
    assert clean.item["metadata"][WORKFLOW_QUARANTINE_KEY]


@pytest.mark.asyncio
async def test_refresh_preserves_explicit_workflow_b_and_a_history() -> None:
    state = RefreshState()
    observation_b = _item(2)
    await state.refresh(observation_b)
    quarantine = state.row["metadata"][WORKFLOW_QUARANTINE_KEY]
    provenance_b = workflow_eligibility_provenance(
        observation_b,
        stage=WorkflowStage.DECISION_CREATED,
        workspace_id="workspace-a",
        decision_id=84,
    )
    state.row.update(
        decision_id=84,
        status="decision_created",
        metadata={
            **state.row["metadata"],
            DECISION_PROVENANCE_KEY: provenance_b,
        },
    )

    patch = await state.refresh(observation_b)

    assert state.row["decision_id"] == 84
    assert patch[DECISION_PROVENANCE_KEY]["decision_id"] == 84
    assert patch[WORKFLOW_QUARANTINE_KEY] == quarantine
