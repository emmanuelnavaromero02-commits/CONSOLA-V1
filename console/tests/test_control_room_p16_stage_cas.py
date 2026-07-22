from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services.control_room.business_action_approval import (
    ApprovableDecision,
    approve_business_item,
)
from app.services.control_room.business_decision_persistence import (
    persist_option_selection,
)
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    ELIGIBILITY_POLICY_VERSION,
    WorkflowStage,
    business_observation_fingerprint,
)


USER = {"id": 7, "email": "owner@example.com"}


def _item():
    return {
        "id": "item-1",
        "kind": "anomaly",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "owner_user_id": 7,
        "decision_id": 42,
        "title": "Measured anomaly",
        "recommendation": "Review measured anomaly",
        "source_dataset": "gold_people",
        "source_system": "sap_hcm",
        "cartridge": "sap_hcm",
        "observed_value": 1,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-20",
        **runtime_row_evidence_fields(
            source_dataset="gold_people",
            source_system="sap_hcm",
            cartridge="sap_hcm",
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            source_row={"item_id": "item-1", "observed_value": 1},
            locator_field="item_id",
            observed_at="2026-07-20",
            business_observation={
                "id": "item-1",
                "metric_type": "count",
                "observed_value": 1,
                "observation_date": "2026-07-20",
            },
        ),
    }


def _provenance(item, stage):
    return {
        "policy_version": ELIGIBILITY_POLICY_VERSION,
        "stage": stage.value,
        "workspace_id": "workspace-a",
        "item_id": item["id"],
        "kind": item["kind"],
        "fingerprint": business_observation_fingerprint(item),
        "eligible_at_link": True,
        "decision_id": 42,
    }


@pytest.mark.asyncio
async def test_option_selection_cannot_degrade_approved_stage():
    item = _item()
    approved = _provenance(item, WorkflowStage.APPROVED)

    class Connection:
        async def fetchrow(self, sql, *_args):
            if "FOR UPDATE" in sql:
                return {
                    "item_id": item["id"],
                    "owner_user_id": 7,
                    "decision_id": 42,
                    "status": "approved",
                    "metadata": json.dumps({DECISION_PROVENANCE_KEY: approved}),
                }
            raise AssertionError("option update reached for approved workflow")

    event = AsyncMock()
    with (
        patch(
            "app.services.control_room.business_workflow_stage_cas.lock_authoritative_business_item",
            AsyncMock(
                return_value={
                    "item_id": item["id"],
                    "owner_user_id": 7,
                    "decision_id": 42,
                    "status": "approved",
                    "metadata": {DECISION_PROVENANCE_KEY: approved},
                }
            ),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await persist_option_selection(
            Connection(),
            user=USER,
            item=item,
            workspace_id="workspace-a",
            option_id="review",
            terminal_statuses=("approved", "executed", "resolved", "dismissed"),
            ensure_item_row=AsyncMock(),
            record_item_event=event,
        )

    assert exc.value.status_code == 409
    event.assert_not_awaited()


@pytest.mark.asyncio
async def test_approval_locks_stage_before_inserting_action():
    item = _item()
    order = []

    class Connection:
        async def fetchrow(self, sql, *_args):
            assert "INSERT INTO decision_actions" in sql
            order.append("insert_action")
            return {"id": 5}

    async def lock(*_args, **_kwargs):
        order.append("lock")

    async def require(*_args, **_kwargs):
        order.append("require")
        return ApprovableDecision(row={"id": 42}, linked=True)

    async def approve_link(*_args, **_kwargs):
        order.append("approve_link")

    with (
        patch(
            "app.services.control_room.business_action_approval.lock_authoritative_business_item",
            side_effect=lock,
        ),
        patch(
            "app.services.control_room.business_action_approval.require_approvable_decision",
            side_effect=require,
        ),
        patch(
            "app.services.control_room.business_action_approval._record_event",
            AsyncMock(),
        ),
        patch(
            "app.services.control_room.business_action_approval._persist_lessons",
            AsyncMock(),
        ),
    ):
        await approve_business_item(
            Connection(),
            user=USER,
            item=item,
            workspace_id="workspace-a",
            decision_id=42,
            lessons=("measured",),
            confidence=0.9,
            ensure_item_row=AsyncMock(),
            link_decision=AsyncMock(),
            approve_link=approve_link,
        )

    assert order == ["require", "lock", "insert_action", "approve_link"]


@pytest.mark.asyncio
async def test_option_selection_zero_row_cas_is_conflict_not_not_found():
    item = _item()
    decision = _provenance(item, WorkflowStage.DECISION_CREATED)

    class Connection:
        calls = 0

        async def fetchrow(self, sql, *_args):
            self.calls += 1
            if "FOR UPDATE" in sql:
                return {
                    "item_id": item["id"],
                    "owner_user_id": 7,
                    "decision_id": 42,
                    "status": "decision_created",
                    "metadata": {DECISION_PROVENANCE_KEY: decision},
                }
            if sql.lstrip().startswith("UPDATE control_room_items"):
                return None
            raise AssertionError(sql)

    with (
        patch(
            "app.services.control_room.business_workflow_stage_cas.lock_authoritative_business_item",
            AsyncMock(
                return_value={
                    "item_id": item["id"],
                    "owner_user_id": 7,
                    "decision_id": 42,
                    "status": "decision_created",
                    "metadata": {DECISION_PROVENANCE_KEY: decision},
                }
            ),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await persist_option_selection(
            Connection(),
            user=USER,
            item=item,
            workspace_id="workspace-a",
            option_id="review",
            terminal_statuses=("approved", "executed", "resolved", "dismissed"),
            ensure_item_row=AsyncMock(),
            record_item_event=AsyncMock(),
        )

    assert exc.value.status_code == 409
