from __future__ import annotations

import json
from contextlib import AbstractAsyncContextManager
from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service
from app.services.control_room.business_runtime_evidence import (
    runtime_row_evidence_fields,
)


USER = {
    "id": 7,
    "email": "ops@example.com",
    "active_tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "active_workspace_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
}


def _item() -> dict:
    item = {
        "id": "business-1",
        "kind": "anomaly",
        "title": "Anomalia valida",
        "entity_label": "Entidad 1",
        "description": "Descripcion",
        "recommendation": "Revisar",
        "source_dataset": "gold_metrics",
        "source_system": "sap_hcm",
        "cartridge": "sap_hcm",
        "tenant_id": USER["active_tenant_id"],
        "workspace_id": USER["active_workspace_id"],
        "severity": "high",
        "status": "open",
        "metric_type": "count",
        "observed_value": 1,
        "population_count": 10,
        "observation_date": "2026-07-20",
    }
    item.update(
        runtime_row_evidence_fields(
            source_dataset="gold_metrics",
            source_system="sap_hcm",
            cartridge="sap_hcm",
            tenant_id=USER["active_tenant_id"],
            workspace_id=USER["active_workspace_id"],
            source_row={"item_id": item["id"], "observed_value": 1},
            locator_field="item_id",
            observed_at="2026-07-20T00:00:00Z",
        )
    )
    return item


class _Context(AbstractAsyncContextManager):
    def __init__(self, value, on_exit=None) -> None:
        self.value = value
        self.on_exit = on_exit

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        if self.on_exit:
            self.on_exit(exc_type)
        return False


class TransactionalConnection:
    def __init__(self, linked_item_id: str | None) -> None:
        self.linked_item_id = linked_item_id
        self.fetchrow_calls = 0
        self.committed = False
        self.rolled_back = False

    def transaction(self):
        def finish(exc_type):
            self.rolled_back = exc_type is not None
            self.committed = exc_type is None

        return _Context(self, finish)

    async def execute(self, sql: str, *_args):
        command = sql.lstrip().split(maxsplit=1)[0].upper()
        return "INSERT 0 1" if command == "INSERT" else "SELECT 1"

    async def fetchrow(self, sql: str, *_args):
        self.fetchrow_calls += 1
        normalized = " ".join(sql.split()).upper()
        if "FOR UPDATE" in normalized:
            return {
                "item_id": "business-1",
                "decision_id": None,
                "selected_option_id": None,
                "owner_user_id": 7,
                "metadata": {},
            }
        if normalized.startswith("INSERT INTO DECISIONS"):
            return {"id": 42, "title": "Anomalia valida"}
        if normalized.startswith("INSERT INTO DECISION_ACTIONS"):
            return {"id": 8}
        if normalized.startswith("UPDATE CONTROL_ROOM_ITEMS"):
            return (
                {"item_id": self.linked_item_id}
                if self.linked_item_id is not None
                else None
            )
        raise AssertionError(normalized)


class TransactionalPool:
    __module__ = "asyncpg.pool"

    def __init__(self, connection: TransactionalConnection) -> None:
        self.connection = connection

    def acquire(self):
        return _Context(self.connection)


class LegacyTechnicalConnection(TransactionalConnection):
    def __init__(self) -> None:
        super().__init__(linked_item_id="business-1")
        self.item_row = {
            "item_id": "business-1",
            "item_kind": "source_state",
            "source_dataset": "diagnostic_sources",
            "tenant_id": USER["active_tenant_id"],
            "workspace_id": USER["active_workspace_id"],
            "metadata": {"data_status": "missing", "legacy_note": "keep"},
            "status": "open",
        }
        self.ensure_sql = ""

    async def execute(self, sql: str, *args):
        normalized = " ".join(sql.split())
        if normalized.startswith("INSERT INTO control_room_items"):
            payload = json.loads(args[0])
            self.ensure_sql = normalized
            self.item_row.update(
                {
                    "item_kind": payload["item_kind"],
                    "source_dataset": payload["source_dataset"],
                    "metadata": {
                        "legacy_note": self.item_row["metadata"]["legacy_note"],
                        **payload["metadata"],
                    },
                }
            )
            return "INSERT 0 1"
        return await super().execute(sql, *args)

    async def fetchrow(self, sql: str, *args):
        normalized = " ".join(sql.split()).upper()
        if "FOR UPDATE" in normalized:
            return {
                **self.item_row,
                "decision_id": self.item_row.get("decision_id"),
                "selected_option_id": self.item_row.get("selected_option_id"),
                "owner_user_id": 7,
            }
        if normalized.startswith("UPDATE CONTROL_ROOM_ITEMS"):
            assert self.item_row["item_kind"] == "anomaly"
            assert self.item_row["source_dataset"] == "gold_metrics"
            assert self.item_row["metadata"]["data_status"] == "ready"
            self.item_row["status"] = "decision_created"
            self.item_row["decision_id"] = args[0]
            return {"item_id": self.item_row["item_id"]}
        return await super().fetchrow(sql, *args)


@pytest.mark.asyncio
async def test_control_room_decision_link_failure_rolls_back_and_skips_success_audit():
    connection = TransactionalConnection(linked_item_id=None)
    audit = AsyncMock()
    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=_item()),
        ),
        patch.object(
            control_room_service.auth,
            "pool",
            new=AsyncMock(return_value=TransactionalPool(connection)),
        ),
        patch.object(
            control_room_service,
            "_ensure_item_row",
            new=AsyncMock(),
        ),
        patch.object(
            control_room_service,
            "_record_item_event",
            new=AsyncMock(),
        ),
        patch.object(control_room_service.audit_service, "record_event", audit),
    ):
        with pytest.raises(RuntimeError, match="decision link was not persisted"):
            await control_room_service.create_decision_for_item("business-1", USER)

    assert connection.rolled_back is True
    assert connection.committed is False
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_control_room_decision_creation_commits_only_after_exact_link():
    connection = TransactionalConnection(linked_item_id="business-1")
    audit = AsyncMock()
    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=_item()),
        ),
        patch.object(
            control_room_service.auth,
            "pool",
            new=AsyncMock(return_value=TransactionalPool(connection)),
        ),
        patch.object(
            control_room_service,
            "_ensure_item_row",
            new=AsyncMock(),
        ),
        patch.object(
            control_room_service,
            "_record_item_event",
            new=AsyncMock(),
        ),
        patch.object(control_room_service.audit_service, "record_event", audit),
    ):
        result = await control_room_service.create_decision_for_item(
            "business-1",
            USER,
        )

    assert result["decision"]["id"] == 42
    assert connection.committed is True
    assert connection.rolled_back is False
    audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_technical_row_is_semantically_upserted_before_atomic_link():
    connection = LegacyTechnicalConnection()
    item = {**_item(), "data_status": "ready"}
    audit = AsyncMock()
    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value=item),
        ),
        patch.object(
            control_room_service.auth,
            "pool",
            new=AsyncMock(return_value=TransactionalPool(connection)),
        ),
        patch.object(control_room_service.audit_service, "record_event", audit),
    ):
        result = await control_room_service.create_decision_for_item(
            "business-1",
            USER,
        )

    assert result["decision"]["id"] == 42
    assert connection.committed is True
    assert connection.item_row["status"] == "decision_created"
    assert connection.item_row["decision_id"] == 42
    assert connection.item_row["metadata"]["legacy_note"] == "keep"
    assert "item_kind = EXCLUDED.item_kind" in connection.ensure_sql
    assert "source_dataset = EXCLUDED.source_dataset" in connection.ensure_sql
    assert "control_room_items.metadata - $2::text[]" in connection.ensure_sql
