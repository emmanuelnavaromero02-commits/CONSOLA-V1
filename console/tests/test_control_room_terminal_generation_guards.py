from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services.control_room.business_action_approval import (
    require_approvable_decision,
)
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
    WorkflowStage,
    workflow_eligibility_provenance,
)
from app.services.control_room.business_workflow_stage_cas import (
    select_option_with_stage_cas,
)


USER = {"id": 7, "email": "owner@example.com"}


def _item(value: int = 2) -> dict:
    item = {
        "id": "item-1",
        "kind": "anomaly",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "owner_user_id": 7,
        "decision_id": 84,
        "source_dataset": "gold_people",
        "source_system": "sap_hcm",
        "cartridge": "sap_hcm",
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


def _provenance(item: dict) -> dict:
    return workflow_eligibility_provenance(
        item,
        stage=WorkflowStage.DECISION_CREATED,
        workspace_id="workspace-a",
        decision_id=84,
    )


def _locked(item: dict, execution_status: str) -> dict:
    return {
        "item_id": item["id"],
        "owner_user_id": 7,
        "decision_id": 84,
        "status": "decision_created",
        "execution_status": execution_status,
        "metadata": {DECISION_PROVENANCE_KEY: _provenance(item)},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("execution_status", ("executed", "resolved", "terminal"))
async def test_option_selection_rejects_terminal_execution_with_stale_provenance(
    execution_status,
):
    item = _item()

    class NoUpdate:
        async def fetchrow(self, sql, *_args):
            raise AssertionError(f"terminal workflow reached option update: {sql}")

    with (
        patch(
            "app.services.control_room.business_workflow_stage_cas.lock_authoritative_business_item",
            AsyncMock(return_value=_locked(item, execution_status)),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await select_option_with_stage_cas(
            NoUpdate(),
            user=USER,
            item=item,
            workspace_id="workspace-a",
            option_id="review",
            terminal_statuses=("approved", "resolved"),
            ensure_item_row=AsyncMock(),
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_option_selection_sql_guards_terminal_execution_race():
    item = _item()

    class RaceConnection:
        async def fetchrow(self, sql, *args):
            assert "COALESCE(execution_status, 'not_started')" in sql
            assert {"executed", "resolved", "terminal"}.issubset(set(args[8]))
            return None

    with (
        patch(
            "app.services.control_room.business_workflow_stage_cas.lock_authoritative_business_item",
            AsyncMock(return_value=_locked(item, "not_started")),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await select_option_with_stage_cas(
            RaceConnection(),
            user=USER,
            item=item,
            workspace_id="workspace-a",
            option_id="review",
            terminal_statuses=("approved", "resolved"),
            ensure_item_row=AsyncMock(),
        )

    assert exc.value.status_code == 409


class ApprovalConnection:
    def __init__(self, row: dict) -> None:
        self.row = deepcopy(row)

    async def fetchrow(self, sql: str, *_args):
        if "FROM decisions" in sql:
            return {"id": 84, "created_by_id": 7}
        if "item_id <> $3" in sql:
            return None
        if "FROM control_room_items" in sql:
            return deepcopy(self.row)
        raise AssertionError(sql)


def _approval_row(item: dict, quarantined_fingerprint: str) -> dict:
    return {
        **item,
        "selected_option_id": None,
        "metadata": {
            DECISION_PROVENANCE_KEY: _provenance(item),
            WORKFLOW_QUARANTINE_KEY: {
                "generations": [{"fingerprint": quarantined_fingerprint}]
            },
        },
    }


@pytest.mark.asyncio
async def test_approval_ignores_quarantine_from_prior_generation():
    current = _item(2)
    prior_fingerprint = _provenance(_item(1))["fingerprint"]

    result = await require_approvable_decision(
        ApprovalConnection(_approval_row(current, prior_fingerprint)),
        user=USER,
        item=current,
        workspace_id="workspace-a",
        decision_id=84,
    )

    assert result.linked is True


@pytest.mark.asyncio
async def test_approval_rejects_quarantine_for_current_generation():
    current = _item(2)
    current_fingerprint = _provenance(current)["fingerprint"]

    with pytest.raises(HTTPException) as exc:
        await require_approvable_decision(
            ApprovalConnection(_approval_row(current, current_fingerprint)),
            user=USER,
            item=current,
            workspace_id="workspace-a",
            decision_id=84,
        )

    assert exc.value.status_code == 409
