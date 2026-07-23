from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room.business_action_approval import (
    approve_business_item,
    require_approvable_decision,
)
from app.services.control_room.business_decision_persistence import (
    create_and_link_decision,
)
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    ELIGIBILITY_POLICY_VERSION,
    WorkflowStage,
    business_observation_fingerprint,
)


USER = {
    "id": 7,
    "email": "owner@example.com",
    "active_tenant_id": "tenant-a",
    "active_workspace_id": "workspace-a",
}
TERMINAL_STATUSES = ("approved", "dismissed", "resolved")


def _item(*, status: str = "open") -> dict:
    return {
        "id": "item-1",
        "kind": "anomaly",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "owner_user_id": 7,
        "status": status,
        "title": "Measured anomaly",
        "entity_label": "Employee",
        "description": "Measured headcount gap",
        "recommendation": "Review staffing",
        "severity": "high",
        "source_dataset": "gold_people",
        "source_system": "sap_hcm",
        "cartridge": "sap_hcm",
        "observed_value": 1,
        "metric_type": "count",
        "population_count": 10,
        "observation_date": "2026-07-20",
    }


def _provenance(item: dict, decision_id: int = 42) -> dict:
    return {
        "policy_version": ELIGIBILITY_POLICY_VERSION,
        "stage": WorkflowStage.DECISION_CREATED.value,
        "workspace_id": "workspace-a",
        "item_id": item["id"],
        "kind": item["kind"],
        "fingerprint": business_observation_fingerprint(item),
        "eligible_at_link": True,
        "decision_id": decision_id,
    }


def _is_dml(statement: str) -> bool:
    return statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))


class TerminalCreateConnection:
    def __init__(self, status: str) -> None:
        self.status = status
        self.statements: list[str] = []

    async def fetchrow(self, sql: str, *_args):
        normalized = " ".join(sql.split())
        self.statements.append(normalized)
        if "SELECT item_id, status" in normalized and "FOR UPDATE" in normalized:
            return {"item_id": "item-1", "status": self.status}
        raise AssertionError(
            f"terminal create reached unexpected statement: {normalized}"
        )

    async def execute(self, sql: str, *_args):
        self.statements.append(" ".join(sql.split()))
        raise AssertionError("terminal create performed DML")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", TERMINAL_STATUSES)
async def test_create_link_rejects_locked_terminal_before_dml(status: str) -> None:
    conn = TerminalCreateConnection(status)
    ensure = AsyncMock()
    event = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await create_and_link_decision(
            conn,
            user=USER,
            item=_item(),
            workspace_id="workspace-a",
            ensure_item_row=ensure,
            record_item_event=event,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "terminal_item"
    assert exc.value.detail["status"] == status
    assert "FOR UPDATE" in conn.statements[0]
    assert not any(_is_dml(statement) for statement in conn.statements)
    ensure.assert_not_awaited()
    event.assert_not_awaited()


class OpenCreateConnection:
    def __init__(self) -> None:
        self.prelocked = False

    async def fetchrow(self, sql: str, *_args):
        normalized = " ".join(sql.split())
        if "SELECT item_id, status" in normalized and "FOR UPDATE" in normalized:
            self.prelocked = True
            return {"item_id": "item-1", "status": "open"}
        if "SELECT item_id, decision_id" in normalized and "FOR UPDATE" in normalized:
            return {
                "item_id": "item-1",
                "decision_id": None,
                "selected_option_id": None,
                "owner_user_id": 7,
                "metadata": {},
                "status": "open",
                "execution_status": "not_started",
            }
        if normalized.startswith("INSERT INTO decisions"):
            return {"id": 42, "title": "Measured anomaly"}
        if normalized.startswith("INSERT INTO decision_actions"):
            return {"id": 8}
        raise AssertionError(normalized)


@pytest.mark.asyncio
async def test_open_item_creates_and_links_after_prelock() -> None:
    conn = OpenCreateConnection()

    async def ensure(*_args, **_kwargs):
        assert conn.prelocked is True

    event = AsyncMock()
    link = AsyncMock()
    with patch(
        "app.services.control_room.business_decision_persistence.link_control_room_decision",
        link,
    ):
        decision = await create_and_link_decision(
            conn,
            user=USER,
            item=_item(),
            workspace_id="workspace-a",
            ensure_item_row=ensure,
            record_item_event=event,
        )

    assert decision["id"] == 42
    link.assert_awaited_once()
    event.assert_awaited_once()


class ApprovalConnection:
    def __init__(self, status: str) -> None:
        self.status = status
        self.statements: list[str] = []

    async def fetchrow(self, sql: str, *_args):
        normalized = " ".join(sql.split())
        self.statements.append(normalized)
        if "FROM decisions" in normalized:
            return {"id": 42, "created_by_id": 7}
        if "FROM control_room_items" in normalized and "FOR UPDATE" in normalized:
            item = _item(status=self.status)
            return {
                "item_id": "item-1",
                "decision_id": 42,
                "selected_option_id": None,
                "owner_user_id": 7,
                "item_kind": "anomaly",
                "status": self.status,
                "metadata": {DECISION_PROVENANCE_KEY: _provenance(item)},
            }
        if "item_id <> $3" in normalized:
            return None
        if _is_dml(normalized):
            raise AssertionError("terminal approval performed DML")
        raise AssertionError(normalized)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", TERMINAL_STATUSES)
async def test_approve_rejects_locked_terminal_before_action_or_event(
    status: str,
) -> None:
    conn = ApprovalConnection(status)
    lock = AsyncMock()
    link = AsyncMock()
    approve_link = AsyncMock()
    event = AsyncMock()
    with (
        patch(
            "app.services.control_room.business_action_approval.lock_authoritative_business_item",
            lock,
        ),
        patch(
            "app.services.control_room.business_action_approval._record_event",
            event,
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await approve_business_item(
            conn,
            user=USER,
            item=_item(status=status),
            workspace_id="workspace-a",
            decision_id=42,
            lessons=(),
            confidence=0.9,
            ensure_item_row=AsyncMock(),
            link_decision=link,
            approve_link=approve_link,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "terminal_item"
    assert "FOR UPDATE" in conn.statements[1]
    assert not any(_is_dml(statement) for statement in conn.statements)
    lock.assert_not_awaited()
    link.assert_not_awaited()
    approve_link.assert_not_awaited()
    event.assert_not_awaited()


@pytest.mark.asyncio
async def test_decision_created_link_remains_approvable() -> None:
    approved = await require_approvable_decision(
        ApprovalConnection("decision_created"),
        user=USER,
        item=_item(status="decision_created"),
        workspace_id="workspace-a",
        decision_id=42,
    )

    assert approved.linked is True


async def _scoped(conn, _user, work):
    return await work(conn, "tenant-a", "workspace-a")


@pytest.mark.asyncio
async def test_dismissed_cannot_implicitly_approve_without_reopen_or_audit():
    conn = TerminalCreateConnection("dismissed")
    ensure = AsyncMock()
    event = AsyncMock()
    audit = AsyncMock()
    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            AsyncMock(return_value=_item(status="dismissed")),
        ),
        patch.object(control_room_service.auth, "pool", AsyncMock(return_value=conn)),
        patch.object(control_room_service, "_run_with_db_scope", _scoped),
        patch.object(control_room_service, "_ensure_item_row", ensure),
        patch.object(control_room_service, "_record_item_event", event),
        patch.object(control_room_service, "_lessons_for_item", return_value=[]),
        patch.object(
            control_room_service, "_impact_for_item", return_value={"confidence": 0.9}
        ),
        patch.object(control_room_service.audit_service, "record_event", audit),
        pytest.raises(HTTPException) as exc,
    ):
        await control_room_service.approve_item("item-1", USER)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "terminal_item"
    assert not any(_is_dml(statement) for statement in conn.statements)
    ensure.assert_not_awaited()
    event.assert_not_awaited()
    audit.assert_not_awaited()
