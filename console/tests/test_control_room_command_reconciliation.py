from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service
from app.services.control_room.business_item_reader import resolve_business_item_lookup
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
)


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
        "data_status": "missing",
        "title": "Source missing",
    }


def _owned_diagnostic(owner_user_id: int) -> dict:
    return {**_diagnostic(), "owner_user_id": owner_user_id}


def _business() -> dict:
    return {
        "id": "item-1",
        "kind": "anomaly",
        "cartridge": "sap_hcm",
        "domain": "People",
        "source_dataset": "gold_people",
        "title": "Valid anomaly",
        "description": "Measured anomaly.",
        "recommendation": "Review.",
        "entity_label": "Employee 1",
        "severity": "medium",
        "observed_value": 1,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-16",
        "evidence_refs": ["gold_people:item-1"],
    }


def _derived_business() -> dict:
    return {
        **_business(),
        "id": "derived-1",
        "kind": "intelligence_signal",
        "item_kind": "intelligence_signal",
        "parent_item_id": "parent-1",
    }


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
        load_persisted=AsyncMock(return_value=_owned_diagnostic(7)),
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
    parent = {**_business(), "id": "parent-1"}
    collect_items = AsyncMock()
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

    async def fetchrow(self, sql: str, *args):
        if "INSERT INTO decisions" in sql:
            return {"id": 42, "title": "Decision"}
        if "INSERT INTO decision_actions" in sql:
            return {"id": 100}
        if "INSERT INTO control_room_items" in sql:
            return {"item_id": "item-1"}
        if "UPDATE control_room_items" in sql and "decision_id" in sql:
            self.link_metadata = json.loads(args[3])
            self.link_owner = args[4]
            return {"item_id": "item-1"}
        return {"id": 1}

    async def execute(self, *_args):
        return "INSERT 0 1"


class DecisionPool:
    def __init__(self, conn: DecisionConn) -> None:
        self.conn = conn

    async def acquire(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


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
            new=AsyncMock(return_value=DecisionPool(conn)),
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
            new=AsyncMock(return_value=_owned_diagnostic(7)),
        ),
        patch.object(
            control_room_service,
            "_collect_items",
            new=AsyncMock(return_value={"items": [_business()], "diagnostics": []}),
        ),
        patch.object(
            control_room_service.auth,
            "pool",
            new=AsyncMock(return_value=DecisionPool(conn)),
        ),
        patch.object(control_room_service, "_run_with_db_scope", new=scoped),
        patch.object(
            control_room_service.audit_service, "record_event", new=AsyncMock()
        ),
    ):
        result = await control_room_service.create_decision_for_item("item-1", admin)

    assert result["decision"]["id"] == 42
    assert conn.link_owner == 7
