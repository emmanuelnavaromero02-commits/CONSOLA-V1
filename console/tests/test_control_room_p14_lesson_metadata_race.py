from __future__ import annotations

import json
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service


TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "22222222-2222-2222-2222-222222222222"
USER = {
    "id": 7,
    "email": "ops@example.com",
    "role": "analyst",
    "active_tenant_id": TENANT,
    "active_workspace_id": WORKSPACE,
}
CONCURRENT_RULE = "Preserve the concurrently recorded rule."


def _item() -> dict[str, Any]:
    return {
        "id": "lesson-race-1",
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "owner_user_id": 7,
        "kind": "anomaly",
        "cartridge": "sap_hcm",
        "source_dataset": "gold_people",
        "anomaly_type": "headcount_variance",
        "status": "open",
        "data_status": "ready",
        "metric_type": "count",
        "observed_value": 3,
        "population_count": 10,
        "observation_date": "2026-07-21",
        "evidence_refs": [{"type": "dataset_row", "source_record_id": "row-1"}],
        "omega": {"lessons": {"rules": ["Rule from the stale item."]}},
    }


class ConcurrentMetadataConnection:
    def __init__(self) -> None:
        self.metadata = {"learned_rules": [CONCURRENT_RULE]}
        self.written_metadata: list[dict[str, Any]] = []
        self.lock_count = 0

    async def fetchrow(self, sql: str, *_args: Any) -> dict[str, Any]:
        if "FROM control_room_items" in sql and "FOR UPDATE" in sql:
            self.lock_count += 1
            return {"metadata": self.metadata}
        if "record_prediction_outcome" in sql:
            return {
                "outcome": {
                    "id": 81,
                    "signal_id": "lesson-race-1",
                    "predicted_value": None,
                    "prediction_error": None,
                    "learned_rule": "Outcome adds a current rule.",
                    "metadata": {"input_classification": "observed"},
                    "evaluation_status": "hit",
                    "evaluated_by": "omega_outcome_evaluator.v1",
                    "evaluated_at": "2026-07-31T00:00:00Z",
                },
                "inserted": True,
            }
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def execute(self, sql: str, *args: Any) -> str:
        if "UPDATE control_room_items" in sql:
            payload = next(
                json.loads(value)
                for value in args
                if isinstance(value, str) and value.startswith("{")
            )
            self.written_metadata.append(payload)
            self.metadata.update(payload)
            return "UPDATE 1"
        if "INSERT INTO control_room_item_events" in sql:
            return "INSERT 0 1"
        return "INSERT 0 1"


async def _scope(pool: Any, _user: dict, work: Any) -> Any:
    return await work(pool, TENANT, WORKSPACE)


def _patch_runtime(conn: ConcurrentMetadataConnection) -> tuple[Any, ...]:
    return (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            AsyncMock(return_value=_item()),
        ),
        patch.object(control_room_service.auth, "pool", AsyncMock(return_value=conn)),
        patch.object(control_room_service, "_run_with_db_scope", _scope),
        patch.object(control_room_service, "_ensure_item_row", AsyncMock()),
        patch.object(control_room_service, "_persist_lessons", AsyncMock()),
        patch.object(control_room_service, "_record_item_event", AsyncMock()),
        patch.object(control_room_service.audit_service, "record_event", AsyncMock()),
    )


@pytest.mark.asyncio
async def test_create_lesson_merges_rules_from_locked_metadata() -> None:
    conn = ConcurrentMetadataConnection()
    with ExitStack() as stack:
        for runtime_patch in _patch_runtime(conn):
            stack.enter_context(runtime_patch)
        stack.enter_context(
            patch.object(
                control_room_service,
                "_load_lesson_rows",
                AsyncMock(
                    return_value=[
                        {
                            "id": 1,
                            "item_id": "lesson-race-1",
                            "rule": "Manual lesson adds a current rule.",
                        }
                    ]
                ),
            )
        )
        await control_room_service.create_item_lesson(
            "lesson-race-1",
            {"rule": "Manual lesson adds a current rule."},
            USER,
        )

    assert conn.lock_count == 1
    rules = conn.written_metadata[-1]["learned_rules"]
    assert CONCURRENT_RULE in rules
    assert "Manual lesson adds a current rule." in rules


@pytest.mark.asyncio
async def test_record_outcome_merges_rules_from_locked_metadata() -> None:
    conn = ConcurrentMetadataConnection()
    with ExitStack() as stack:
        for runtime_patch in _patch_runtime(conn):
            stack.enter_context(runtime_patch)
        stack.enter_context(
            patch(
                "app.services.intelligence.history.link_outcome_to_snapshot",
                AsyncMock(),
            )
        )
        await control_room_service.record_item_outcome(
            "lesson-race-1",
            {
                "action_taken": "review",
                "learned_rule": "Outcome adds a current rule.",
            },
            USER,
        )

    assert conn.lock_count == 1
    rules = conn.written_metadata[-1]["learned_rules"]
    assert CONCURRENT_RULE in rules
    assert "Outcome adds a current rule." in rules
