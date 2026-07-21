from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.services.control_room.business_access import owner_projection
from app.services.control_room.business_decision_persistence import (
    create_and_link_decision,
    persist_option_selection,
)
from app.services.control_room.business_item_persistence import PERSIST_ITEMS_SQL
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_item_reader import (
    resolve_scoped_business_item_lookup,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    ELIGIBILITY_POLICY_VERSION,
    WORKFLOW_QUARANTINE_KEY,
    WorkflowStage,
    business_observation_fingerprint,
    persistence_metadata,
    workflow_eligibility_provenance,
    workflow_has_eligible_provenance,
)
from app.services.control_room.business_workflow_reconciliation import (
    workflow_metadata_patches,
)


USER = {"id": 7, "tenant_id": "tenant-a", "workspace_id": "workspace-a"}


def _item() -> dict:
    item = {
        "id": "business-1",
        "kind": "anomaly",
        "workspace_id": "workspace-a",
        "tenant_id": "tenant-a",
        "title": "Valid anomaly",
        "entity_label": "Employee",
        "description": "Measured anomaly",
        "recommendation": "Review",
        "source_dataset": "gold_people",
        "source_system": "sap_hcm",
        "cartridge": "sap_hcm",
        "severity": "high",
        "observed_value": 1,
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
            source_row={"item_id": item["id"]},
            locator_field="item_id",
            observed_at=item["observation_date"],
        ),
    }
def test_option_stage_without_decision_is_valid_and_workspace_bound():
    item = _item()
    metadata = {
        "selected_option_id": "review",
        DECISION_PROVENANCE_KEY: {
            "policy_version": ELIGIBILITY_POLICY_VERSION,
            "stage": "option_selected",
            "workspace_id": "workspace-a",
            "item_id": item["id"],
            "kind": item["kind"],
            "fingerprint": business_observation_fingerprint(item),
            "eligible_at_link": True,
            "option_id": "review",
        },
    }
    assert workflow_has_eligible_provenance(metadata, item, decision_id=None)
    assert not workflow_has_eligible_provenance(
        metadata,
        {**item, "workspace_id": "workspace-b"},
        decision_id=None,
    )
@pytest.mark.asyncio
async def test_option_update_failure_rolls_back_before_success_event():
    class BrokenConnection:
        async def fetchrow(self, *_args):
            raise RuntimeError("write failed")

        async def execute(self, *_args):
            raise RuntimeError("write failed")

    ensure = AsyncMock()
    event = AsyncMock()
    with pytest.raises(RuntimeError, match="write failed"):
        await persist_option_selection(
            BrokenConnection(),
            user=USER,
            item=_item(),
            workspace_id="workspace-a",
            option_id="review",
            terminal_statuses=("approved", "resolved"),
            ensure_item_row=ensure,
            record_item_event=event,
        )
    event.assert_not_awaited()


def test_owner_projection_preserves_explicit_legacy_null_owner():
    assert owner_projection(_item(), {"owner_user_id": None}) == {"owner_user_id": None}


def test_refresh_unlinks_ambiguous_legacy_workflow_for_quarantine():
    sql = " ".join(PERSIST_ITEMS_SQL.split())
    assert "THEN NULL ELSE control_room_items.decision_id" in sql
    assert "THEN NULL ELSE control_room_items.selected_option_id" in sql


@pytest.mark.asyncio
async def test_refresh_quarantines_workflow_when_current_observation_is_technical():
    business = {**_item(), "workspace_id": "workspace-a"}
    provenance = workflow_eligibility_provenance(
        business,
        stage=WorkflowStage.DECISION_CREATED,
        workspace_id="workspace-a",
        decision_id=42,
    )

    class Connection:
        async def fetch(self, *_args):
            return [
                {
                    **business,
                    "item_id": business["id"],
                    "item_kind": business["kind"],
                    "decision_id": 42,
                    "status": "decision_created",
                    "execution_status": "not_started",
                    "metadata": {
                        CURRENT_ELIGIBILITY_FINGERPRINT_KEY: provenance["fingerprint"],
                        DECISION_PROVENANCE_KEY: provenance,
                    },
                }
            ]

    technical = {
        **business,
        "item_id": business["id"],
        "item_kind": business["kind"],
        "data_status": "missing",
        "metadata": {"data_status": "missing"},
    }
    patches = await workflow_metadata_patches(Connection(), [technical])
    patch = patches[("workspace-a", business["id"])]
    assert WORKFLOW_QUARANTINE_KEY in patch
    assert DECISION_PROVENANCE_KEY not in patch


@pytest.mark.asyncio
async def test_normal_user_cannot_resolve_legacy_null_owner_but_admin_can():
    persisted = {**_item(), "owner_user_id": None}
    collector = AsyncMock(return_value={"items": [_item()], "diagnostics": []})

    async def load_persisted(_item_id: str):
        return dict(persisted)

    async def scoped(_pool, _user, work):
        return await work(AsyncMock(), "tenant-a", "workspace-a")

    common = {
        "load_persisted": load_persisted,
        "collect_items": collector,
        "normalize_lineage": dict,
        "pool_factory": AsyncMock(return_value=object()),
        "run_scoped": scoped,
    }
    normal, _ = await resolve_scoped_business_item_lookup(
        "business-1",
        {
            "id": 7,
            "active_tenant_id": "tenant-a",
            "active_workspace_id": "workspace-a",
        },
        **common,
    )
    admin, _ = await resolve_scoped_business_item_lookup(
        "business-1",
        {
            "id": 9,
            "workspace_role": "workspace_admin",
            "active_tenant_id": "tenant-a",
            "active_workspace_id": "workspace-a",
        },
        **common,
    )
    assert normal is None
    assert admin and admin["owner_user_id"] is None


@pytest.mark.asyncio
async def test_decision_creation_locks_item_before_inserting_decision():
    calls: list[str] = []

    class OrderingConnection:
        async def fetchrow(self, sql: str, *_args):
            normalized = " ".join(sql.split()).upper()
            if "FOR UPDATE" in normalized:
                calls.append("lock")
                return {
                    "item_id": "business-1",
                    "decision_id": None,
                    "metadata": {},
                    "owner_user_id": 7,
                }
            if normalized.startswith("INSERT INTO DECISIONS"):
                calls.append("decision")
                return {"id": 42, "title": "Valid anomaly"}
            if normalized.startswith("INSERT INTO DECISION_ACTIONS"):
                calls.append("action")
                return {"id": 8}
            if normalized.startswith("UPDATE CONTROL_ROOM_ITEMS"):
                calls.append("link")
                return {"item_id": "business-1"}
            raise AssertionError(normalized)

    async def ensure(*_args, **kwargs):
        assert kwargs["status"] == "open"
        assert kwargs["item"]["decision_id"] is None
        assert kwargs["item"]["selected_option_id"] is None
        assert kwargs["item"]["execution_status"] == "not_started"
        calls.append("ensure")

    await create_and_link_decision(
        OrderingConnection(),
        user=USER,
        item=_item(),
        workspace_id="workspace-a",
        ensure_item_row=ensure,
        record_item_event=AsyncMock(),
    )
    assert calls[:3] == ["ensure", "lock", "decision"]


@pytest.mark.parametrize("stage", [WorkflowStage.APPROVED, WorkflowStage.EXECUTED])
def test_approved_and_executed_provenance_require_decision(stage):
    with pytest.raises(ValueError, match="requires decision_id"):
        workflow_eligibility_provenance(
            _item(), stage=stage, workspace_id="workspace-a"
        )


@pytest.mark.asyncio
async def test_option_selection_persists_typed_provenance_before_event():
    captured = {}

    class Connection:
        async def fetchrow(self, sql: str, *args):
            if "FOR UPDATE" in sql:
                item = _item()
                row = dict(
                    item,
                    item_id=item["id"],
                    item_kind=item["kind"],
                    owner_user_id=7,
                    status="open",
                    execution_status="not_started",
                    metadata=dict(item),
                )
                row["metadata"] = persistence_metadata(row)
                return row
            assert "RETURNING item_id" in sql
            captured.update(json.loads(args[0]))
            return {"item_id": "business-1"}

    event = AsyncMock()
    await persist_option_selection(
        Connection(),
        user=USER,
        item={**_item(), "owner_user_id": 7},
        workspace_id="workspace-a",
        option_id="review",
        terminal_statuses=("approved", "resolved"),
        ensure_item_row=AsyncMock(),
        record_item_event=event,
    )
    provenance = captured[DECISION_PROVENANCE_KEY]
    assert provenance["stage"] == "option_selected"
    assert provenance["workspace_id"] == "workspace-a"
    assert provenance["option_id"] == "review"
    assert "decision_id" not in provenance
    event.assert_awaited_once()
