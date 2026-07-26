import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from console.tests import control_room_execution_helpers as execution_helpers


TENANT = "11111111-1111-1111-1111-111111111111"
WORKSPACE = "22222222-2222-2222-2222-222222222222"
USER = {
    "id": 7,
    "email": "ops@example.com",
    "role": "admin",
    "allowed_cartridges": ["sap_hcm"],
    "active_tenant_id": TENANT,
    "active_workspace_id": WORKSPACE,
}


def _item() -> dict[str, Any]:
    return {
        "id": "business-race-1",
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "owner_user_id": 7,
        "kind": "anomaly",
        "cartridge": "sap_hcm",
        "source_system": "sap_hcm",
        "source_dataset": "gold_people",
        "anomaly_type": "headcount_variance",
        "status": "open",
        "data_status": "ready",
        "metric_type": "count",
        "observed_value": 3,
        "population_count": 10,
        "observation_date": "2026-07-21",
        "evidence_refs": ["gold_people:row-1"],
        "alert_state": {"state": "open", "ticket": "initial"},
        "lesson_applications": [],
        "omega": {
            "control": {"items": [{"id": "control-1", "status": "open"}]},
            "lessons": {"rules": [], "applied": []},
        },
    }


class MutationConnection:
    def __init__(self) -> None:
        self.lock_reached = False
        self.authoritative: dict[str, Any] = {}
        self.payloads: list[dict[str, Any]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        if "UPDATE control_room_items" in sql:
            assert self.lock_reached, "mutation happened before the lock boundary"
            if "'{control_state}'" in sql:
                control_id = str(args[0])
                state = self.authoritative.setdefault("control_state", {})
                state[control_id] = {
                    **json.loads(args[1]),
                    **state.get(control_id, {}),
                    **json.loads(args[2]),
                }
                self.payloads.append({"control_state": state})
            elif "'{alert_state}'" in sql:
                current = self.authoritative.setdefault("alert_state", {})
                current.update(
                    {**json.loads(args[0]), **current, **json.loads(args[1])}
                )
                self.payloads.append({"alert_state": current})
            for value in args:
                if not isinstance(value, str) or not value.startswith("{"):
                    continue
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    self.payloads.append(parsed)
            return "UPDATE 1"
        if "INSERT INTO control_room_item_events" in sql:
            return "INSERT 0 1"
        return "SELECT 1"

    async def fetchrow(self, _sql: str, *_args: Any) -> dict[str, Any]:
        assert self.lock_reached, "authoritative state read before the lock boundary"
        return {"metadata": self.authoritative}


async def _scope(pool: Any, _user: dict, work: Any) -> Any:
    return await work(pool, TENANT, WORKSPACE)


async def _race_at_lock(conn: MutationConnection, **_kwargs: Any) -> None:
    conn.lock_reached = True


def _public_item(item: dict, _builder: Any, **updates: Any) -> dict[str, Any]:
    projected = {**item, **updates}
    if state := updates.get("control_state"):
        projected["omega"] = {"control": {"items": list(state.values())}}
    return projected


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["control", "alert", "lesson"])
async def test_mutations_merge_authoritative_state(operation: str) -> None:
    item = _item()
    conn = MutationConnection()
    lesson = {
        "id": 1,
        "item_id": item["id"],
        "cartridge_id": item["cartridge"],
        "anomaly_type": item["anomaly_type"],
        "rule": "Review the current authoritative state first.",
    }
    concurrent = {
        "control": {"id": "control-concurrent", "status": "closed"},
        "alert": {"concurrent_ticket": "ticket-2"},
        "lesson": {"lesson_id": 99, "rule": "Concurrent lesson remains."},
    }[operation]
    if operation == "control":
        conn.authoritative = {"control_state": {concurrent["id"]: concurrent}}
    elif operation == "alert":
        conn.authoritative = {"alert_state": concurrent}
    else:
        conn.authoritative = {
            "lesson_applications": [concurrent],
            "learned_rules": [concurrent["rule"]],
        }

    async def call() -> Any:
        if operation == "control":
            return await control_room_service.update_item_control(
                item["id"], "control-1", {"status": "closed"}, USER
            )
        if operation == "alert":
            return await control_room_service.acknowledge_alert(
                item["id"], USER, body={"note": "triage"}
            )
        return await control_room_service.apply_item_lesson(
            item["id"], 1, {"note": "apply"}, USER
        )

    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            AsyncMock(return_value=dict(item)),
        ),
        patch.object(control_room_service.auth, "pool", AsyncMock(return_value=conn)),
        patch.object(control_room_service, "_run_with_db_scope", _scope),
        patch.object(control_room_service, "_ensure_item_row", _race_at_lock),
        patch.object(control_room_service, "_project_public_item", _public_item),
        patch.object(
            control_room_service,
            "_load_lesson_rows",
            AsyncMock(return_value=[lesson]),
        ),
        patch.object(
            control_room_service,
            "_alert_for_item",
            return_value={"id": item["id"], "status": "open"},
        ),
        patch.object(control_room_service.audit_service, "record_event", AsyncMock()),
    ):
        await call()

    key = {"control": "control_state", "alert": "alert_state"}.get(
        operation, "lesson_applications"
    )
    payload = next(value for value in conn.payloads if key in value)
    if operation == "control":
        assert concurrent["id"] in payload[key]
    elif operation == "alert":
        assert payload[key]["concurrent_ticket"] == concurrent["concurrent_ticket"]
    else:
        assert concurrent in payload[key]


class _Context:
    def __init__(self, value: Any, *, enter: Any = None, exit: Any = None) -> None:
        self.value = value
        self.on_enter = enter
        self.on_exit = exit

    async def __aenter__(self) -> Any:
        if self.on_enter:
            self.on_enter()
        return self.value

    async def __aexit__(self, *_args: Any) -> None:
        if self.on_exit:
            self.on_exit()


class TransactionConnection:
    def __init__(self, pool: "TransactionPool", name: str) -> None:
        self.pool = pool
        self.name = name
        self.in_transaction = False

    def transaction(self) -> _Context:
        return _Context(
            self,
            enter=lambda: setattr(self, "in_transaction", True),
            exit=lambda: setattr(self, "in_transaction", False),
        )

    async def execute(self, sql: str, *_args: Any) -> str:
        self.pool.calls.append((self.name, self.in_transaction, sql))
        return (
            "INSERT 0 1"
            if "INSERT INTO control_room_item_events" in sql
            else "UPDATE 1"
            if "UPDATE control_room_items" in sql
            else "SELECT 1"
        )

    async def fetchrow(self, sql: str, *_args: Any) -> dict[str, Any] | None:
        self.pool.calls.append((self.name, self.in_transaction, sql))
        if "INSERT INTO prediction_outcomes" in sql:
            return {"id": 81, "metadata": {}, "signal_id": "business-race-1"}
        return self.pool.locked_row


class TransactionPool:
    __module__ = "asyncpg.pool"

    def __init__(self, locked_row: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, bool, str]] = []
        self.locked_row = locked_row
        self.count = 0

    def acquire(self) -> _Context:
        self.count += 1
        return _Context(TransactionConnection(self, f"conn-{self.count}"))


@pytest.mark.asyncio
async def test_outcome_and_event_share_one_connection_and_transaction() -> None:
    pool = TransactionPool()
    with (
        patch.object(
            control_room_service, "_item_for_mutation", AsyncMock(return_value=_item())
        ),
        patch.object(control_room_service.auth, "pool", AsyncMock(return_value=pool)),
        patch.object(control_room_service, "_ensure_item_row", AsyncMock()),
        patch(
            "app.services.intelligence.history.link_outcome_to_snapshot", AsyncMock()
        ),
        patch.object(control_room_service.audit_service, "record_event", AsyncMock()),
    ):
        await control_room_service.record_item_outcome(
            "business-race-1", {"action_taken": "review"}, USER
        )

    outcome = next(call for call in pool.calls if "prediction_outcomes" in call[2])
    event = next(call for call in pool.calls if "control_room_item_events" in call[2])
    assert outcome[0] == event[0]
    assert outcome[1] is event[1] is True


@pytest.mark.asyncio
async def test_auto_run_revalidates_generation_before_final_event() -> None:
    original = execution_helpers.explicit_action(
        execution_helpers.runtime_evidenced_item(_item()),
        template_id="create_followup_task",
    )[0]
    changed = {**original, "observed_value": 4}
    pool = TransactionPool(locked_row=changed)
    selected = {**original, "selected_option_id": "remediate", "status": "in_review"}
    decided = {**selected, "decision_id": 42, "status": "decision_created"}
    dry_run = {**decided, "execution_status": "dry_run_validated"}
    with (
        patch.multiple(
            control_room_service,
            _item_for_mutation=AsyncMock(return_value=original),
            record_item_step=AsyncMock(return_value={}),
            select_item_option=AsyncMock(return_value={"item": selected}),
            create_decision_for_item=AsyncMock(
                return_value={"decision": {"id": 42}, "item": decided}
            ),
            action_preview=AsyncMock(
                return_value={"execution": {"id": 1}, "result": {}}
            ),
            action_dry_run=AsyncMock(
                return_value={"execution": {"id": 2}, "result": {}, "item": dry_run}
            ),
        ),
        patch.object(control_room_service.auth, "pool", AsyncMock(return_value=pool)),
        patch.object(control_room_service.audit_service, "record_event", AsyncMock()),
    ):
        with pytest.raises(HTTPException) as error:
            await control_room_service.run_auto_item(original["id"], USER)

    assert error.value.status_code == 409
    assert all("auto_run_completed" not in sql for _, _, sql in pool.calls)
