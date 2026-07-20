from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service


USER = {
    "id": 7,
    "active_tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "active_workspace_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
}


class RecordingPool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, *args):
        self.calls.append((" ".join(sql.split()), args))
        if sql.lstrip().upper().startswith("INSERT INTO CONTROL_ROOM_ITEMS"):
            payload = json.loads(args[0]) if args else []
            return f"INSERT 0 {len(payload)}"
        return None


def _item(index: int) -> dict:
    return {
        "id": f"item-{index}",
        "kind": "anomaly",
        "cartridge": "sap_hcm",
        "domain": "Recursos Humanos",
        "source_dataset": "gold_workforce",
        "title": f"Item {index}",
        "severity": "medium",
        "status": "open",
        "observation_date": "2026-07-16",
        "observed_value": index + 1,
    }


@pytest.mark.asyncio
async def test_one_thousand_items_use_one_batched_upsert():
    pool = RecordingPool()
    items = [_item(index) for index in range(1000)]
    with patch.object(
        control_room_service.auth,
        "pool",
        new=AsyncMock(return_value=pool),
    ):
        await control_room_service._persist_item_state(items, USER)

    inserts = [call for call in pool.calls if call[0].upper().startswith("INSERT INTO")]
    assert len(inserts) == 1
    payload = json.loads(inserts[0][1][0])
    assert len(payload) == 1000
    assert {row["workspace_id"] for row in payload} == {USER["active_workspace_id"]}
    assert {row["tenant_id"] for row in payload} == {USER["active_tenant_id"]}
    assert "ON CONFLICT (workspace_id, item_id) DO UPDATE" in inserts[0][0]


@pytest.mark.asyncio
async def test_batch_retry_remains_one_statement_per_attempt():
    pool = RecordingPool()
    items = [_item(index) for index in range(1000)]
    with patch.object(
        control_room_service.auth,
        "pool",
        new=AsyncMock(return_value=pool),
    ):
        await control_room_service._persist_item_state(items, USER)
        await control_room_service._persist_item_state(items, USER)

    inserts = [
        sql for sql, _args in pool.calls if sql.upper().startswith("INSERT INTO")
    ]
    assert len(inserts) == 2


@pytest.mark.asyncio
async def test_diagnostic_metadata_drops_business_only_fields_in_batch():
    pool = RecordingPool()
    diagnostic = {
        **_item(1),
        "kind": "source_state",
        "data_status": "missing",
        "omega": {"options": [{"id": "repair"}]},
        "decision_id": 99,
        "priority": {"score": 100},
        "priority_score": 100,
        "selected_option_id": "repair",
    }
    with patch.object(
        control_room_service.auth,
        "pool",
        new=AsyncMock(return_value=pool),
    ):
        await control_room_service._persist_item_state([diagnostic], USER)

    insert = next(call for call in pool.calls if call[0].upper().startswith("INSERT"))
    row = json.loads(insert[1][0])[0]
    assert row["item_kind"] == "source_state"
    assert row["selected_option_id"] is None
    assert row["impact_estimate"] is None
    assert "omega" not in row["metadata"]
    assert "priority" not in row["metadata"]
    assert "decision_id" not in row["metadata"]


@pytest.mark.asyncio
async def test_duplicate_item_ids_are_persisted_as_diagnostics():
    pool = RecordingPool()
    duplicate = _item(7)
    with patch.object(
        control_room_service.auth,
        "pool",
        new=AsyncMock(return_value=pool),
    ):
        await control_room_service._persist_item_state(
            [duplicate, {**duplicate, "title": "Conflicting duplicate"}],
            USER,
        )

    insert = next(call for call in pool.calls if call[0].upper().startswith("INSERT"))
    row = json.loads(insert[1][0])[0]
    assert row["metadata"]["data_status"] == "invalid_schema"
    assert row["impact_estimate"] is None


@pytest.mark.asyncio
async def test_non_finite_diagnostic_value_does_not_abort_batch_json():
    pool = RecordingPool()
    invalid = {**_item(1), "observed_value": float("nan")}
    with patch.object(
        control_room_service.auth,
        "pool",
        new=AsyncMock(return_value=pool),
    ):
        await control_room_service._persist_item_state([invalid, _item(2)], USER)

    insert = next(call for call in pool.calls if call[0].upper().startswith("INSERT"))
    rows = json.loads(insert[1][0])
    assert len(rows) == 2
    assert rows[0]["metadata"]["observed_value"] is None
