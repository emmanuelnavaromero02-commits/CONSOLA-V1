from __future__ import annotations

from unittest.mock import AsyncMock

from app.services.control_room.business_action_reservation import effective_action_key
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_action_replay import action_reservation_contract
from app.services.control_room.business_execution_precondition import (
    execution_authorization_contract,
)
from app.services.control_room.execution import _execution_payload
from console.tests.control_room_execution_helpers import (
    USER,
    authoritative_item_row,
    dry_run_action_run_row,
    execution_row,
    successful_command_tag,
)


def execution_fetchrow_router(
    item: dict,
    *,
    template_id: str = "create_followup_task",
    execution_status: str = "executed",
    decision_exists: bool = True,
    dry_run_exists: bool = True,
    existing_reservation: bool = False,
    action_row: dict | None = None,
):
    input_payload = _execution_payload(
        item,
        "execute_live",
        dict(ACTION_TEMPLATES[template_id]),
    )
    key = effective_action_key(
        workspace_id="workspace-A",
        item=item,
        template_id=template_id,
        operation="execute",
        provided="idem-1" if template_id == "create_followup_task" else None,
        input_payload=input_payload,
    )
    pending = {
        "id": 55,
        "status": "completed" if existing_reservation else "pending",
        "idempotency_key": key,
        "metadata": {
            "reservation_contract": action_reservation_contract(
                workspace_id="workspace-A",
                item=item,
                template_id=template_id,
                operation="execute",
                authorization_contract=execution_authorization_contract(USER),
                input_payload=input_payload,
            )
        },
        "execution_result": {
            "ok": True,
            "executed": True,
            "target": "decision_actions",
        },
    }

    def route(query: object, *_args: object):
        sql = " ".join(str(query).split()).upper()
        if "FROM CONTROL_ROOM_ACTION_TEMPLATES" in sql:
            template = ACTION_TEMPLATES[template_id]
            return {
                "template_id": template_id,
                "cartridge_id": template["cartridge_id"],
                "label": template["label"],
                "requires_approval": template["requires_approval"],
            }
        if "FROM CONTROL_ROOM_ITEMS" in sql and "FOR UPDATE" in sql:
            return authoritative_item_row(
                item,
                decision_id=item.get("decision_id"),
                status=item.get("status"),
                selected_option_id=item.get("selected_option_id"),
                execution_status=item.get("execution_status"),
            )
        if "MODE = 'DRY_RUN'" in sql:
            return (
                dry_run_action_run_row(item, template_id=template_id)
                if dry_run_exists
                else None
            )
        if sql.startswith("INSERT INTO ACTION_RUNS"):
            return None if existing_reservation else pending
        if sql.startswith("SELECT * FROM ACTION_RUNS"):
            return pending if existing_reservation else None
        if "FROM ACTION_RUNS" in sql and "FOR UPDATE" in sql:
            return {**pending, "status": "pending"}
        if sql.startswith("UPDATE ACTION_RUNS"):
            if "REMOTE_ATTEMPT" in sql:
                pending["metadata"] = {
                    **pending["metadata"],
                    "remote_attempt": {
                        "status": "started",
                        "adapter": "TestAdapter",
                        "target": "external_system",
                    },
                }
                return {**pending, "status": "pending"}
            return {**pending, "status": "completed"}
        if "FROM DECISIONS" in sql:
            return {"id": 42} if decision_exists else None
        if sql.startswith("INSERT INTO DECISION_ACTIONS"):
            return action_row or {"id": 101, "decision_id": 42}
        if sql.startswith("INSERT INTO CONTROL_ROOM_ACTION_EXECUTIONS"):
            return execution_row(item, status=execution_status, template_id=template_id)
        return None

    return route


class TransactionalConn:
    def __init__(self, fetchrow_side_effect):
        self.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
        self.fetch = AsyncMock(return_value=[])
        self.fetchval = AsyncMock(return_value=0)
        self.execute = AsyncMock(side_effect=successful_command_tag)
        self.acquired = False
        self.released = False
        self.transaction_entered = False
        self.transaction_exited = False
        self.transaction_error = None

    def transaction(self):
        return _TransactionContext(self)


class TransactionalPool:
    def __init__(self, *, pool_fetchrow_side_effect, conn_fetchrow_side_effect):
        self.fetchrow = AsyncMock(side_effect=pool_fetchrow_side_effect)
        self.fetch = AsyncMock(return_value=[])
        self.fetchval = AsyncMock(return_value=0)
        self.execute = AsyncMock(side_effect=successful_command_tag)
        self.conn = TransactionalConn(conn_fetchrow_side_effect)

    def acquire(self):
        return _AcquireContext(self.conn)


class _AcquireContext:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.acquired = True
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        self.conn.released = True
        return False


class _TransactionContext:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.transaction_entered = True
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        self.conn.transaction_exited = True
        self.conn.transaction_error = exc_type
        return False
