from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.control_room_direct_actions import (
    EXCEPTION_APPROVED_MESSAGE,
    ExceptionApprovalRequest,
    ExceptionReopenRequest,
)
from app.services.control_room import business_direct_action_authority as authority
from app.services.control_room import business_exception_approval as service
from app.services.control_room.business_action_direct_contract import (
    direct_contract_from_persisted_row,
)
from app.services.control_room.business_action_handle import ResolvedActionHandle
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_direct_action_authority import (
    DirectActionReplay,
    LockedDirectAction,
    operation_digest,
)
from control_room_direct_action_fixtures import (
    authorization,
    direct_row,
    exception_item,
    exception_row,
)
from control_room_surface_fixtures import OPERATOR, WORKSPACE_ID, business_item


HANDLE = "e" * 64
TOKEN_ID = "22222222-2222-2222-2222-222222222222"
REASON = "Proveedor validado por auditoría interna"


class TxConn:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fail_on = fail_on
        self.committed = False

    def _record(self, query: str, args: tuple[Any, ...]) -> str:
        statement = " ".join(str(query).split())
        self.calls.append((statement, args))
        if self.fail_on and self.fail_on in statement:
            raise RuntimeError("database write failed")
        return statement

    @asynccontextmanager
    async def transaction(self):
        yield
        self.committed = True

    def is_in_transaction(self) -> bool:
        return False

    async def execute(self, query: str, *args: Any) -> str:
        statement = self._record(query, args)
        if statement.startswith("UPDATE"):
            return "UPDATE 1"
        if statement.startswith("INSERT"):
            return "INSERT 0 1"
        return "SELECT 1"

    async def fetchrow(self, query: str, *args: Any):
        statement = self._record(query, args)
        if "FROM control_room_items" in statement and "FOR UPDATE" in statement:
            return {"item_id": "business-1", "status": "open"}
        if statement.startswith("UPDATE control_room_action_tokens"):
            return {"id": TOKEN_ID}
        return None

    async def fetch(self, query: str, *args: Any):
        self._record(query, args)
        return []

    def statements(self, prefix: str) -> list[tuple[str, tuple[Any, ...]]]:
        return [call for call in self.calls if call[0].startswith(prefix)]


def _locked(item: dict, row: dict, template_id: str) -> LockedDirectAction:
    contract = direct_contract_from_persisted_row(
        row,
        authorization=authorization(),
        template=ACTION_TEMPLATES[template_id],
        user=OPERATOR,
    )
    assert contract is not None
    token = {"id": TOKEN_ID, "template_id": template_id, "item_id": item["id"]}
    return LockedDirectAction(token, contract, row, authorization())


def _patches(conn: TxConn, locked: LockedDirectAction, template_id: str):
    return (
        patch.object(service.auth, "pool", new=AsyncMock(return_value=conn)),
        patch.object(
            service,
            "resolve_business_action_handle",
            new=AsyncMock(
                return_value=ResolvedActionHandle(
                    item_id=locked.contract.item_id,
                    template_id=template_id,
                    binding_id=HANDLE,
                )
            ),
        ),
        patch.object(service, "lock_direct_action", new=AsyncMock(return_value=locked)),
        patch.object(
            service, "find_direct_action_replay", new=AsyncMock(return_value=None)
        ),
        patch.object(service.audit_service, "record_event", new=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_approval_dismisses_records_resolution_event_audit_and_consumes_once():
    item = business_item()
    locked = _locked(item, direct_row(item), "approve_exception")
    conn = TxConn()
    patches = _patches(conn, locked, "approve_exception")
    with patches[0], patches[1] as resolve, patches[2], patches[3], patches[4] as audit:
        response = await service.approve_exception(
            OPERATOR,
            action_handle=HANDLE,
            reason=REASON,
            idempotency_key="key-1",
            ip="127.0.0.1",
            user_agent="pytest",
        )

    assert response.model_dump() == {
        "action_handle": HANDLE,
        "status": "exception_approved",
        "reversible": True,
        "message": EXCEPTION_APPROVED_MESSAGE,
    }
    resolve.assert_awaited_once()
    assert resolve.await_args.kwargs["allowed_template_ids"] == frozenset(
        {"approve_exception"}
    )
    dismiss = [
        s
        for s, _ in conn.statements("UPDATE control_room_items")
        if "SET status = 'dismissed'" in s
    ]
    assert len(dismiss) == 1
    event = conn.statements("INSERT INTO control_room_item_events")
    assert len(event) == 1
    assert event[0][1][3] == "exception_approved"
    assert json.loads(event[0][1][6]) == {"reason": REASON}
    resolution = [
        (s, args)
        for s, args in conn.calls
        if s.startswith(" ".join(service.RESOLUTION_SQL.split()))
    ]
    assert len(resolution) == 1
    stored = json.loads(resolution[0][1][0])
    assert stored["resolution"] == "exception_approved"
    assert stored["resolution_actor_id"] == 9
    assert stored["resolution_reason"] == REASON
    assert stored["resolution_at"]
    assert stored["resolution_observation_fingerprint"] == (
        locked.contract.observation_fingerprint
    )
    assert stored["resolution_evidence_digest"] == locked.contract.evidence_digest
    assert resolution[0][1][1:] == (WORKSPACE_ID, "business-1", 9)
    audit.assert_awaited_once()
    audit_kwargs = audit.await_args.kwargs
    assert audit_kwargs["connection"] is conn
    assert audit_kwargs["critical"] is True
    assert audit_kwargs["action"] == "control_room.exception.approve"
    assert audit_kwargs["metadata"] == {
        "template_id": "approve_exception",
        "reason": REASON,
    }
    consume = conn.statements("UPDATE control_room_action_tokens")
    assert len(consume) == 1
    assert consume[0][1][0] == operation_digest(
        token_id=TOKEN_ID, template_id="approve_exception", idempotency_key="key-1"
    )
    assert consume[0][1][1] == "exception_approved"
    order = [
        statement.split(" ")[0] + ":" + statement.split(" ")[1]
        for statement, _ in conn.calls
    ]
    assert order.index("UPDATE:control_room_action_tokens") == len(order) - 1


@pytest.mark.asyncio
async def test_failure_before_consumption_never_consumes_the_handle():
    item = business_item()
    locked = _locked(item, direct_row(item), "approve_exception")
    conn = TxConn(fail_on="INSERT INTO control_room_item_events")
    patches = _patches(conn, locked, "approve_exception")
    with patches[0], patches[1], patches[2], patches[3], patches[4] as audit:
        with pytest.raises(RuntimeError):
            await service.approve_exception(
                OPERATOR, action_handle=HANDLE, reason=REASON
            )

    assert conn.statements("UPDATE control_room_action_tokens") == []
    audit.assert_not_awaited()
    assert conn.committed is False


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_without_consuming():
    item = business_item()
    locked = _locked(item, direct_row(item), "approve_exception")
    conn = TxConn()
    patches = _patches(conn, locked, "approve_exception")
    with patches[0], patches[1], patches[2], patches[3], patches[4] as audit:
        audit.side_effect = RuntimeError("critical audit table missing")
        with pytest.raises(RuntimeError):
            await service.approve_exception(
                OPERATOR, action_handle=HANDLE, reason=REASON
            )

    assert conn.statements("UPDATE control_room_action_tokens") == []
    assert conn.committed is False


@pytest.mark.asyncio
async def test_idempotent_replay_returns_the_same_result_without_new_writes():
    replay = DirectActionReplay("business-1", "approve_exception", "exception_approved")
    resolve = AsyncMock(side_effect=AssertionError("resolved again"))
    with (
        patch.object(
            service, "find_direct_action_replay", new=AsyncMock(return_value=replay)
        ),
        patch.object(service, "resolve_business_action_handle", new=resolve),
    ):
        response = await service.approve_exception(
            OPERATOR, action_handle=HANDLE, reason=REASON, idempotency_key="key-1"
        )

    assert response.status == "exception_approved"
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_of_a_different_operation_is_a_conflict():
    replay = DirectActionReplay("business-1", "approve_exception", "exception_reopened")
    with patch.object(
        service, "find_direct_action_replay", new=AsyncMock(return_value=replay)
    ):
        with pytest.raises(HTTPException) as exc:
            await service.approve_exception(
                OPERATOR, action_handle=HANDLE, reason=REASON, idempotency_key="key-1"
            )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_reopen_restores_open_status_strips_resolution_and_audits():
    item = exception_item()
    locked = _locked(item, exception_row(item), "reopen_exception")
    conn = TxConn()
    patches = _patches(conn, locked, "reopen_exception")
    with patches[0], patches[1] as resolve, patches[2], patches[3], patches[4] as audit:
        response = await service.reopen_exception(
            OPERATOR, action_handle=HANDLE, reason="Revisar", idempotency_key=None
        )

    assert response.status == "exception_reopened"
    assert resolve.await_args.kwargs["allowed_template_ids"] == frozenset(
        {"reopen_exception"}
    )
    reopen = [s for s, _ in conn.statements("UPDATE control_room_items")]
    assert len(reopen) == 1
    for key in (
        "resolution",
        "resolution_actor_id",
        "resolution_reason",
        "resolution_at",
        "resolution_observation_fingerprint",
        "resolution_evidence_digest",
    ):
        assert f"- '{key}'" in reopen[0]
    assert "status = 'dismissed'" in reopen[0]
    assert "decision_id IS NULL" in reopen[0]
    event = conn.statements("INSERT INTO control_room_item_events")
    assert event[0][1][3] == "exception_reopened"
    assert audit.await_args.kwargs["action"] == "control_room.exception.reopen"
    consume = conn.statements("UPDATE control_room_action_tokens")
    assert consume[0][1][1] == "exception_reopened"


@pytest.mark.parametrize(
    "reason",
    (
        "corto",
        "         ",
        "x" * 501,
        "motivo con\ncontrol",
        "motivo con " + chr(0x202E) + " bidi",
        chr(0x200B) * 10,
        "motivo " + chr(0x200B) * 10,
        "motivo válido" + chr(0x200E),
        "motivo válido" + chr(0x200F),
        "motivo válido" + chr(0x061C),
        "motivo válido" + chr(0x2028) + "otra línea",
        "motivo válido" + chr(0x2029),
        "motivo válido" + chr(0xFEFF),
        12345678901,
        None,
    ),
)
def test_approval_reason_is_mandatory_bounded_and_free_of_controls(reason):
    with pytest.raises(ValidationError):
        ExceptionApprovalRequest.model_validate(
            {"action_handle": HANDLE, "reason": reason}
        )


def test_reason_is_trimmed_and_request_is_strict():
    request = ExceptionApprovalRequest.model_validate(
        {"action_handle": HANDLE, "reason": f"  {REASON}  ", "idempotency_key": " k "}
    )
    assert request.reason == REASON
    assert request.idempotency_key == "k"
    for extra in ({"item_id": "business-1"}, {"template_id": "approve_exception"}):
        with pytest.raises(ValidationError):
            ExceptionApprovalRequest.model_validate(
                {"action_handle": HANDLE, "reason": REASON, **extra}
            )
    with pytest.raises(ValidationError):
        ExceptionApprovalRequest.model_validate(
            {"action_handle": "A" * 64, "reason": REASON}
        )
    ExceptionReopenRequest.model_validate({"action_handle": HANDLE, "reason": "Sí!"})
    with pytest.raises(ValidationError):
        ExceptionReopenRequest.model_validate({"action_handle": HANDLE, "reason": "no"})


class LockConn:
    def __init__(self, *, token: dict | None, row: dict | None) -> None:
        self.token = token
        self.row = row

    async def execute(self, *_args: Any) -> str:
        return "SELECT 1"

    async def fetchrow(self, query: str, *_args: Any):
        if "FROM control_room_action_tokens" in query:
            return self.token
        return None

    async def fetch(self, query: str, *_args: Any):
        if "FROM control_room_items AS item" in query:
            return [self.row] if self.row else []
        return []


def _lock_token(contract, **overrides):
    token = {
        "id": TOKEN_ID,
        "template_id": contract.template_id,
        "item_id": contract.item_id,
        "binding_digest": contract.binding_digest,
        "evidence_digest": contract.evidence_digest,
        "observation_fingerprint": contract.observation_fingerprint,
        "contract_digest": contract.contract_digest,
        "target_digest": contract.target_digest,
        "decision_digest": contract.decision_digest,
        "access_revision_digest": contract.access_revision_digest,
        "rbac_policy_digest": contract.rbac_policy_digest,
    }
    token.update(overrides)
    return token


async def _lock(conn: LockConn, template_id: str = "approve_exception"):
    with patch.object(
        authority,
        "capture_authorization_snapshot",
        new=AsyncMock(return_value=authorization()),
    ):
        return await authority.lock_direct_action(
            conn,
            user=OPERATOR,
            action_handle=HANDLE,
            item_id="business-1",
            template_id=template_id,
        )


@pytest.mark.asyncio
async def test_lock_rechecks_template_authorization_and_contract_under_lock():
    item = business_item()
    row = direct_row(item)
    contract = _locked(item, row, "approve_exception").contract

    locked = await _lock(LockConn(token=_lock_token(contract), row=row))
    assert locked.contract.contract_digest == contract.contract_digest

    cases = (
        (
            LockConn(
                token=_lock_token(contract, template_id="create_decision_proposal"),
                row=row,
            ),
            404,
        ),
        (
            LockConn(token=_lock_token(contract, rbac_policy_digest="c" * 64), row=row),
            404,
        ),
        (LockConn(token=_lock_token(contract), row=None), 409),
        (
            LockConn(token=_lock_token(contract), row={**row, "status": "dismissed"}),
            409,
        ),
        (LockConn(token=_lock_token(contract, binding_digest="d" * 64), row=row), 409),
        (LockConn(token=None, row=row), 404),
    )
    for conn, status in cases:
        with pytest.raises(HTTPException) as exc:
            await _lock(conn)
        assert exc.value.status_code == status

    with pytest.raises(HTTPException) as exc:
        await _lock(
            LockConn(token=_lock_token(contract), row=row), "create_followup_task"
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_reopen_lock_survives_observation_drift_but_not_a_new_approval():
    item = exception_item()
    row = exception_row(item)
    contract = _locked(item, row, "reopen_exception").contract
    drifted = exception_row(
        item,
        metadata_updates={
            "business_eligibility_fingerprint": "f" * 64,
            "data_status": "stale",
        },
    )

    locked = await _lock(
        LockConn(token=_lock_token(contract), row=drifted), "reopen_exception"
    )
    assert locked.contract.contract_digest == contract.contract_digest

    reapproved = exception_row(
        item, metadata_updates={"resolution_at": "2026-09-26T08:00:00+00:00"}
    )
    with pytest.raises(HTTPException) as exc:
        await _lock(
            LockConn(token=_lock_token(contract), row=reapproved), "reopen_exception"
        )
    assert exc.value.status_code == 409
