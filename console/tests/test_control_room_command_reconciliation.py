from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room.business_item_reader import resolve_business_item_lookup
from app.services.control_room.business_policy_metadata import business_policy_metadata
from app.services.control_room.business_workflow_provenance import (
    ELIGIBILITY_POLICY_VERSION,
    ELIGIBILITY_POLICY_VERSION_KEY,
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
    business_observation_fingerprint,
)
from control_room_runtime_evidence_fixture import bind_runtime_row_evidence


USER = {
    "id": 7,
    "email": "ops@example.com",
    "role": "workspace_admin",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}


def _diagnostic() -> dict:
    return {
        "id": "item-1",
        "kind": "source_state",
        "item_kind": "source_state",
        "source_dataset": "gold_people",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "data_status": "missing",
        "title": "Source missing",
    }


def _business(
    item_id: str = "item-1",
    *,
    kind: str = "anomaly",
    parent_item_id: str | None = None,
) -> dict:
    item = {
        "id": item_id,
        "kind": kind,
        "cartridge": "sap_hcm",
        "domain": "People",
        "source_dataset": "gold_people",
        "source_system": "sap_hcm",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "title": "Valid anomaly",
        "description": "Measured anomaly.",
        "recommendation": "Review.",
        "entity_label": "Employee 1",
        "severity": "medium",
        "observed_value": 1,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-16",
    }
    if parent_item_id:
        item["parent_item_id"] = parent_item_id
    return bind_runtime_row_evidence(
        item, locator_field="item_id", observed_at=item["observation_date"]
    )


def _derived_business() -> dict:
    return _business("derived-1", kind="intelligence_signal", parent_item_id="parent-1")


@pytest.mark.asyncio
async def test_diagnostic_persisted_yields_live_business_item():
    item, parents = await resolve_business_item_lookup(
        "item-1",
        load_persisted=AsyncMock(return_value=_diagnostic()),
        collect_items=AsyncMock(
            return_value={"items": [_business()], "diagnostics": []}
        ),
        load_lineage=AsyncMock(return_value=[]),
        normalize_lineage=dict,
    )

    assert item["kind"] == "anomaly"
    assert item["id"] == "item-1"
    assert parents == {"item-1"}


@pytest.mark.asyncio
async def test_live_business_reconciliation_preserves_persisted_owner():
    item, _parents = await resolve_business_item_lookup(
        "item-1",
        load_persisted=AsyncMock(return_value={**_diagnostic(), "owner_user_id": 7}),
        collect_items=AsyncMock(
            return_value={"items": [_business()], "diagnostics": []}
        ),
        load_lineage=AsyncMock(return_value=[]),
        normalize_lineage=dict,
    )

    assert item["kind"] == "anomaly"
    assert item["owner_user_id"] == 7


@pytest.mark.asyncio
async def test_persisted_derived_item_uses_eligible_persisted_parent():
    parent = _business("parent-1")
    collect_items = AsyncMock(return_value={"items": [], "diagnostics": []})
    load_lineage = AsyncMock(return_value=[parent])

    item, parents = await resolve_business_item_lookup(
        "derived-1",
        load_persisted=AsyncMock(return_value=_derived_business()),
        collect_items=collect_items,
        load_lineage=load_lineage,
        normalize_lineage=dict,
    )

    assert item["id"] == "derived-1"
    assert item["kind"] == "intelligence_signal"
    assert parents == {"parent-1"}
    load_lineage.assert_awaited_once_with(["parent-1"])
    collect_items.assert_not_awaited()


class DecisionConn:
    def __init__(self) -> None:
        self.link_metadata: dict | None = None
        self.link_owner: int | None = None
        self.committed = False
        self.row = {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "item_id": "item-1",
            "owner_user_id": 7,
            "cartridge_id": "sap_hcm",
            "source_dataset": "gold_people",
            "item_kind": "source_state",
            "status": "open",
            "decision_id": None,
            "selected_option_id": None,
            "execution_status": "not_started",
            "metadata": {"item_kind": "source_state", "data_status": "missing"},
        }

    async def fetchrow(self, sql: str, *args):
        if "FOR UPDATE" in sql:
            return dict(self.row)
        if "INSERT INTO decisions" in sql:
            return {"id": 42, "title": "Decision"}
        if "INSERT INTO decision_actions" in sql:
            return {"id": 100}
        if "INSERT INTO control_room_items" in sql:
            return {"item_id": "item-1"}
        if "UPDATE control_room_items" in sql and "decision_id" in sql:
            self.link_metadata = json.loads(args[3])
            self.link_owner = args[4]
            self.row.update(
                decision_id=args[0], status=args[5], metadata=self.link_metadata
            )
            return {"item_id": "item-1"}
        return {"id": 1}

    async def execute(self, sql: str, *args):
        if "INSERT INTO control_room_items" in sql:
            self.row.update(json.loads(args[0]))
        return "INSERT 0 1"


@pytest.mark.asyncio
async def test_create_decision_uses_live_business_over_persisted_diagnostic():
    conn = DecisionConn()

    async def scoped(_pool, _user, work):
        result = await work(conn, "tenant-a", "workspace-a")
        conn.committed = True
        return result

    with (
        patch.object(
            control_room_service,
            "_persisted_item_for_mutation",
            new=AsyncMock(return_value=_diagnostic()),
        ),
        patch.object(
            control_room_service,
            "_collect_items",
            new=AsyncMock(return_value={"items": [_business()], "diagnostics": []}),
        ),
        patch.object(
            control_room_service.auth,
            "pool",
            new=AsyncMock(return_value=object()),
        ),
        patch.object(control_room_service, "_run_with_db_scope", new=scoped),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ),
    ):
        result = await control_room_service.create_decision_for_item("item-1", USER)

    assert result["decision"]["id"] == 42
    assert conn.committed is True
    assert conn.link_metadata is not None
    assert conn.link_metadata[DECISION_PROVENANCE_KEY]["eligible_at_link"] is True


@pytest.mark.asyncio
async def test_admin_creates_decision_for_live_item_without_stealing_owner():
    conn = DecisionConn()
    admin = {**USER, "id": 9}

    async def scoped(_pool, _user, work):
        return await work(conn, "tenant-a", "workspace-a")

    with (
        patch.object(
            control_room_service,
            "_persisted_item_for_mutation",
            new=AsyncMock(return_value={**_diagnostic(), "owner_user_id": 7}),
        ),
        patch.object(
            control_room_service,
            "_collect_items",
            new=AsyncMock(return_value={"items": [_business()], "diagnostics": []}),
        ),
        patch.object(
            control_room_service.auth,
            "pool",
            new=AsyncMock(return_value=object()),
        ),
        patch.object(control_room_service, "_run_with_db_scope", new=scoped),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ),
    ):
        result = await control_room_service.create_decision_for_item("item-1", admin)

    assert result["decision"]["id"] == 42
    assert conn.link_owner == 7


@pytest.mark.asyncio
async def test_quarantined_persisted_workflow_without_live_item_is_not_actionable():
    quarantined = {
        **_business(),
        "kind": "intelligence_signal",
        "item_kind": "intelligence_signal",
        "decision_id": 42,
        "execution_status": "dry_run_validated",
        "metadata": {WORKFLOW_QUARANTINE_KEY: {"reason": "fingerprint_mismatch"}},
    }
    with (
        patch.object(
            control_room_service,
            "_persisted_item_for_mutation",
            new=AsyncMock(return_value=quarantined),
        ),
        patch.object(
            control_room_service,
            "_collect_items",
            new=AsyncMock(return_value={"items": [], "diagnostics": []}),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service._item_for_mutation("item-1", USER)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "item_workflow_quarantined"
