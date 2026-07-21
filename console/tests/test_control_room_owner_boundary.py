from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room.business_item_persistence import (
    ENSURE_ITEM_SQL,
    OwnerScopeConflict,
    ensure_item_row,
    persist_item_rows,
)
from app.services.control_room.business_repository import link_control_room_decision


USER = {
    "id": 7,
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}


class OwnerConnection:
    def __init__(self) -> None:
        self.fetch_sql = ""
        self.fetch_args: tuple = ()

    async def fetch(self, sql: str, *args):
        self.fetch_sql = " ".join(sql.split())
        self.fetch_args = args
        return []


@pytest.mark.asyncio
async def test_overlay_state_is_scoped_by_tenant_workspace_and_owner():
    conn = OwnerConnection()

    async def scoped(_pool, _user, work):
        return await work(conn, "tenant-a", "workspace-a")

    item = {
        "id": "business-1",
        "kind": "anomaly",
        "title": "Business item",
        "source_dataset": "gold_metrics",
        "evidence_refs": ["gold_metrics:business-1"],
    }
    with (
        patch.object(
            control_room_service.auth, "pool", new=AsyncMock(return_value=object())
        ),
        patch.object(control_room_service, "_run_with_db_scope", new=scoped),
    ):
        result = await control_room_service._overlay_item_state([item], USER)

    assert result[0]["decision_id"] is None
    assert "owner_user_id" not in result[0]
    assert control_room_service.expected_business_item_owner(result[0], USER) == 7
    assert "tenant_id::text = $3" in conn.fetch_sql
    assert "owner_user_id = $4" in conn.fetch_sql
    assert conn.fetch_args == ("workspace-a", ["business-1"], "tenant-a", 7)


@pytest.mark.asyncio
async def test_workspace_admin_overlay_preserves_persisted_owner():
    class AdminConnection:
        async def fetch(self, sql: str, *args):
            assert "owner_user_id =" not in " ".join(sql.split())
            assert args == ("workspace-a", ["business-1"], "tenant-a")
            return [
                {
                    "item_id": "business-1",
                    "owner_user_id": 7,
                    "status": "open",
                    "metadata": {},
                }
            ]

    async def scoped(_pool, _user, work):
        return await work(AdminConnection(), "tenant-a", "workspace-a")

    admin = {**USER, "id": 9, "workspace_role": "workspace_admin"}
    item = {
        "id": "business-1",
        "kind": "anomaly",
        "title": "Business item",
        "source_dataset": "gold_metrics",
        "evidence_refs": ["gold_metrics:business-1"],
    }
    with (
        patch.object(
            control_room_service.auth, "pool", new=AsyncMock(return_value=object())
        ),
        patch.object(control_room_service, "_run_with_db_scope", new=scoped),
    ):
        result = await control_room_service._overlay_item_state([item], admin)

    assert result[0]["owner_user_id"] == 7
    assert control_room_service.expected_business_item_owner(result[0], admin) == 7


class ConflictConnection:
    async def execute(self, sql: str, *_args):
        assert sql == ENSURE_ITEM_SQL
        return "INSERT 0 0"

    async def fetchrow(self, sql: str, *args):
        assert "owner_user_id IS NOT DISTINCT FROM $5" in sql
        assert args[4] == 7
        return None


@pytest.mark.asyncio
async def test_owner_conflict_aborts_upsert_and_decision_link():
    conn = ConflictConnection()
    with pytest.raises(OwnerScopeConflict):
        await ensure_item_row(conn, {"item_id": "business-1"}, terminal_statuses=())

    with pytest.raises(RuntimeError, match="decision link was not persisted"):
        await link_control_room_decision(
            conn,
            workspace_id="workspace-a",
            item_id="business-1",
            decision_id=42,
            owner_user_id=7,
            item={
                "id": "business-1",
                "kind": "anomaly",
                "source_dataset": "gold_metrics",
                "observed_value": 1,
                "observation_date": "2026-07-20",
                "evidence_refs": ["gold_metrics:business-1"],
            },
        )


@pytest.mark.asyncio
async def test_non_admin_batch_rejects_payload_for_another_owner():
    conn = AsyncMock()

    with pytest.raises(OwnerScopeConflict):
        await persist_item_rows(
            conn,
            [{"item_id": "business-1", "owner_user_id": 7}],
            owner_scope_id=9,
        )

    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_persisted_lookup_applies_tenant_workspace_scope_and_returns_not_found():
    class PersistedConnection:
        async def fetchrow(self, sql: str, *args):
            assert "owner_user_id =" not in sql
            assert args == ("workspace-a", "business-1", "tenant-a")
            return None

    async def scoped(_pool, _user, work):
        return await work(PersistedConnection(), "tenant-a", "workspace-a")

    with (
        patch.object(
            control_room_service.auth, "pool", new=AsyncMock(return_value=object())
        ),
        patch.object(control_room_service, "_run_with_db_scope", new=scoped),
    ):
        item = await control_room_service._persisted_item_for_mutation(
            "business-1", USER
        )

    assert item is None


@pytest.mark.asyncio
async def test_foreign_owner_is_not_found_before_live_collection():
    class PersistedConnection:
        async def fetchrow(self, _sql: str, *_args):
            return {"item_id": "business-1", "owner_user_id": 9}

    async def scoped(_pool, _user, work):
        return await work(PersistedConnection(), "tenant-a", "workspace-a")

    collector = AsyncMock(return_value={"items": [], "diagnostics": []})
    with (
        patch.object(
            control_room_service.auth, "pool", new=AsyncMock(return_value=object())
        ),
        patch.object(control_room_service, "_run_with_db_scope", new=scoped),
        patch.object(control_room_service, "_collect_items", new=collector),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await control_room_service._item_for_mutation("business-1", USER)

    assert exc_info.value.status_code == 404
    collector.assert_not_awaited()
