from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room.business_action_mutations import command_count
from app.services.control_room.business_item_persistence import (
    PersistenceCommandTagError,
)


USER = {
    "id": 7,
    "email": "ops@example.com",
    "role": "user",
    "active_tenant_id": "tenant-A",
    "active_workspace_id": "workspace-A",
}


@pytest.mark.parametrize("tag", [None, "UPDATE", "UPDATE 1 trailing", "INSERT 1 1"])
def test_mutation_command_tags_fail_closed(tag):
    command = (
        "INSERT" if isinstance(tag, str) and tag.startswith("INSERT") else "UPDATE"
    )
    with pytest.raises(PersistenceCommandTagError):
        command_count(tag, command)


def _item() -> dict:
    return {
        "id": "item-1",
        "kind": "anomaly",
        "cartridge": "sap_hcm",
        "domain": "Recursos Humanos",
        "source_dataset": "employees_anomalies",
        "entity_kind": "Empleado",
        "entity_id": "1001",
        "entity_label": "Empleado 1001",
        "anomaly_type": "terminated_but_active",
        "title": "Empleado terminado sigue activo",
        "description": "Validar acceso.",
        "recommendation": "Revisar baja.",
        "severity": "critical",
        "status": "open",
        "owner_user_id": 7,
        "detected_at": "2026-07-16T10:00:00Z",
        "omega": {
            "options": [{"id": "review"}],
            "control": {"items": [{"id": "control-1", "status": "open"}]},
        },
    }


async def _scoped(pool, user, work):
    del user
    return await work(pool, "tenant-A", "workspace-A")


class _ZeroMutationConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, sql: str, *args):
        del args
        self.statements.append(sql)
        if "UPDATE control_room_items" in sql:
            return "UPDATE 0"
        if "INSERT INTO control_room_item_events" in sql:
            return "INSERT 0 0"
        return "SELECT 1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,args,kwargs",
    [
        ("record_item_step", ("item-1", "signals", USER), {}),
        (
            "update_item_control",
            ("item-1", "control-1", {"status": "closed"}, USER),
            {},
        ),
        ("dismiss_item", ("item-1", USER), {}),
        ("reopen_item", ("item-1", USER), {}),
        ("acknowledge_alert", ("item-1", USER), {}),
        ("snooze_alert", ("item-1", USER), {}),
        ("assign_alert", ("item-1", USER), {}),
        ("mark_alert_false_positive", ("item-1", USER), {}),
    ],
)
async def test_command_update_zero_rolls_back_without_success_audit(
    command, args, kwargs
):
    conn = _ZeroMutationConnection()
    audit = AsyncMock()
    with (
        patch.object(
            control_room_service, "_item_for_mutation", AsyncMock(return_value=_item())
        ),
        patch.object(control_room_service.auth, "pool", AsyncMock(return_value=conn)),
        patch.object(control_room_service, "_run_with_db_scope", _scoped),
        patch.object(control_room_service, "_ensure_item_row", AsyncMock()),
        patch.object(
            control_room_service, "_alert_for_item", return_value={"id": "item-1"}
        ),
        patch.object(control_room_service.audit_service, "record_event", audit),
    ):
        with pytest.raises(HTTPException) as exc:
            await getattr(control_room_service, command)(*args, **kwargs)

    assert exc.value.status_code == 404
    assert not any("control_room_item_events" in sql for sql in conn.statements)
    audit.assert_not_awaited()


class _ForeignDecisionConnection:
    def __init__(
        self, *, created_by_id: int = 7, decision_visible: bool = True
    ) -> None:
        self.created_by_id = created_by_id
        self.decision_visible = decision_visible
        self.inserted = False

    async def fetchrow(self, sql: str, *args):
        if "FROM decisions" in sql:
            if not self.decision_visible:
                return None
            return {
                "id": 91,
                "created_by_id": self.created_by_id,
                "kpis": [
                    {
                        "provenance": {
                            "type": "decision_provenance",
                            "version": 1,
                            "origin": "control_room",
                            "item_id": "other-item",
                        }
                    }
                ],
            }
        if "FROM control_room_items" in sql:
            return {
                "item_id": "item-1",
                "decision_id": None,
                "owner_user_id": 7,
                "item_kind": "anomaly",
                "metadata": {},
            }
        if "INSERT INTO decision_actions" in sql:
            self.inserted = True
            return {"id": 1, "decision_id": 91, "ts": None}
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "created_by_id,decision_visible,status_code",
    [(7, True, 409), (22, True, 404), (7, False, 404)],
)
async def test_approve_rejects_foreign_decision_before_action_or_audit(
    created_by_id, decision_visible, status_code
):
    conn = _ForeignDecisionConnection(
        created_by_id=created_by_id,
        decision_visible=decision_visible,
    )
    audit = AsyncMock()
    with (
        patch.object(
            control_room_service, "_item_for_mutation", AsyncMock(return_value=_item())
        ),
        patch.object(control_room_service.auth, "pool", AsyncMock(return_value=conn)),
        patch.object(control_room_service, "_run_with_db_scope", _scoped),
        patch.object(control_room_service, "_ensure_item_row", AsyncMock()),
        patch.object(
            control_room_service, "approve_control_room_decision", AsyncMock()
        ),
        patch.object(control_room_service, "_record_item_event", AsyncMock()),
        patch.object(control_room_service, "_persist_lessons", AsyncMock()),
        patch.object(control_room_service.audit_service, "record_event", audit),
    ):
        with pytest.raises(HTTPException) as exc:
            await control_room_service.approve_item("item-1", USER, decision_id=91)

    assert exc.value.status_code == status_code
    assert conn.inserted is False
    audit.assert_not_awaited()


class _EventZeroConnection(_ZeroMutationConnection):
    async def execute(self, sql: str, *args):
        del args
        self.statements.append(sql)
        if "UPDATE control_room_items" in sql:
            return "UPDATE 1"
        if "INSERT INTO control_room_item_events" in sql:
            return "INSERT 0 0"
        return "SELECT 1"


@pytest.mark.asyncio
async def test_event_insert_zero_aborts_before_success_audit():
    conn = _EventZeroConnection()
    audit = AsyncMock()
    with (
        patch.object(
            control_room_service, "_item_for_mutation", AsyncMock(return_value=_item())
        ),
        patch.object(control_room_service.auth, "pool", AsyncMock(return_value=conn)),
        patch.object(control_room_service, "_run_with_db_scope", _scoped),
        patch.object(control_room_service, "_ensure_item_row", AsyncMock()),
        patch.object(control_room_service.audit_service, "record_event", audit),
    ):
        with pytest.raises(RuntimeError, match="insert affected unexpected rows"):
            await control_room_service.record_item_step("item-1", "signals", USER)

    audit.assert_not_awaited()


class _LinkedDecisionConnection:
    async def fetchrow(self, sql: str, *args):
        del args
        if "FROM decisions" in sql:
            return {"id": 91, "created_by_id": 7, "kpis": []}
        if "item_id <> $3" in sql:
            return None
        if "FROM control_room_items" in sql:
            return {
                "item_id": "item-1",
                "decision_id": 91,
                "owner_user_id": 7,
                "item_kind": "anomaly",
                "metadata": {},
            }
        if "INSERT INTO decision_actions" in sql:
            return {"id": 3, "decision_id": 91, "ts": None}
        return None

    async def execute(self, sql: str, *args):
        del args
        if "INSERT INTO" in sql:
            return "INSERT 0 1"
        return "SELECT 1"


@pytest.mark.asyncio
async def test_approve_accepts_decision_already_linked_to_same_owned_item():
    conn = _LinkedDecisionConnection()
    ensure = AsyncMock()
    approve_link = AsyncMock()
    link_decision = AsyncMock()
    audit = AsyncMock()
    with (
        patch.object(
            control_room_service, "_item_for_mutation", AsyncMock(return_value=_item())
        ),
        patch.object(control_room_service.auth, "pool", AsyncMock(return_value=conn)),
        patch.object(control_room_service, "_run_with_db_scope", _scoped),
        patch.object(control_room_service, "_ensure_item_row", ensure),
        patch.object(control_room_service, "link_control_room_decision", link_decision),
        patch.object(
            control_room_service, "approve_control_room_decision", approve_link
        ),
        patch.object(control_room_service, "_lessons_for_item", return_value=[]),
        patch.object(
            control_room_service, "_impact_for_item", return_value={"confidence": 0.9}
        ),
        patch.object(
            control_room_service,
            "_project_public_item",
            side_effect=lambda item, _builder, **updates: {**item, **updates},
        ),
        patch.object(control_room_service.audit_service, "record_event", audit),
    ):
        result = await control_room_service.approve_item("item-1", USER, decision_id=91)

    assert result["approved"] is True
    ensure.assert_awaited_once()
    assert ensure.await_args.kwargs["critical"] is True
    link_decision.assert_not_awaited()
    approve_link.assert_awaited_once()
    audit.assert_awaited_once()
