from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service
from app.services.control_room.business_approve_with_optional_decision import (
    approve_with_optional_decision,
)
from app.services.control_room.business_council_actors import CouncilMaker
from app.services.control_room.business_item_approval_guard import (
    DENIAL_MESSAGES,
    DENIED_AUDIT_ACTION,
    IMPLICIT_SELF_APPROVAL,
    SELF_APPROVAL,
    ItemApprovalDenied,
)
from test_control_room_permissions import _build_real_control_room_router_client


USER = {
    "id": 7,
    "email": "ops@example.com",
    "role": "user",
    "workspace_role": "workspace_admin",
    "active_tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "active_workspace_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
}


def _item() -> dict:
    return {
        "id": "item-1",
        "kind": "anomaly",
        "tenant_id": USER["active_tenant_id"],
        "workspace_id": USER["active_workspace_id"],
        "owner_user_id": 7,
        "cartridge": "sap_hcm",
        "source_dataset": "gold_people",
        "title": "Measured anomaly",
        "entity_label": "Employee",
        "description": "Measured headcount gap",
        "recommendation": "Review staffing",
        "severity": "high",
        "status": "decision_created",
        "decision_id": 42,
    }


def _approve(*, decision_id, run_scoped, approve_item, audit):
    return approve_with_optional_decision(
        object(),
        user=USER,
        item=_item(),
        decision_id=decision_id,
        lessons=("rule",),
        confidence=0.9,
        run_scoped=run_scoped,
        ensure_item_row=AsyncMock(),
        approve_item=approve_item,
        link_decision=AsyncMock(),
        approve_link=AsyncMock(),
        record_audit_event=audit,
    )


def test_empty_approve_body_is_rejected_before_service() -> None:
    approve = AsyncMock()
    with patch.object(control_room_service, "approve_item", approve):
        response = _build_real_control_room_router_client(USER).post(
            "/api/control-room/items/item-1/approve",
            headers={"authorization": "Bearer test"},
            content=b"",
        )

    assert response.status_code == 422
    approve.assert_not_awaited()


@pytest.mark.asyncio
async def test_implicit_approval_is_denied_and_audited_before_any_scope() -> None:
    run_scope = AsyncMock()
    approve_business = AsyncMock()
    audit = AsyncMock()
    with (
        patch.object(
            control_room_service,
            "_item_for_mutation",
            AsyncMock(return_value=_item()),
        ),
        patch.object(
            control_room_service.auth, "pool", AsyncMock(return_value=object())
        ),
        patch.object(control_room_service, "_run_with_db_scope", run_scope),
        patch.object(control_room_service, "_approve_business_item", approve_business),
        patch.object(control_room_service, "_lessons_for_item", return_value=["rule"]),
        patch.object(
            control_room_service, "_impact_for_item", return_value={"confidence": 0.9}
        ),
        patch.object(control_room_service.audit_service, "record_event", audit),
        pytest.raises(ItemApprovalDenied) as exc,
    ):
        await control_room_service.approve_item("item-1", USER)

    assert exc.value.status_code == 403
    assert exc.value.detail == {
        "code": IMPLICIT_SELF_APPROVAL,
        "message": DENIAL_MESSAGES[IMPLICIT_SELF_APPROVAL],
    }
    run_scope.assert_not_awaited()
    approve_business.assert_not_awaited()
    audit.assert_awaited_once()
    kwargs = audit.await_args.kwargs
    assert "connection" not in kwargs
    assert kwargs["action"] == DENIED_AUDIT_ACTION
    assert kwargs["status"] == "denied"
    assert kwargs["resource_id"] == "item-1"
    assert kwargs["critical"] is True
    assert kwargs["metadata"] == {
        "reason": IMPLICIT_SELF_APPROVAL,
        "decision_id": None,
        "maker": None,
    }


@pytest.mark.asyncio
async def test_denial_inside_scope_is_audited_after_rollback() -> None:
    order: list[str] = []

    async def run_scope(_pool, _user, work):
        order.append("begin")
        try:
            return await work(object(), "tenant-a", "workspace-a")
        except Exception:
            order.append("rollback")
            raise

    async def approve_business(*_args, **_kwargs):
        order.append("guard")
        raise ItemApprovalDenied(
            SELF_APPROVAL, decision_id=42, maker=CouncilMaker.person(7)
        )

    async def audit(**kwargs):
        assert "connection" not in kwargs
        order.append(f"audit:{kwargs['action']}")

    with pytest.raises(ItemApprovalDenied) as exc:
        await _approve(
            decision_id=42,
            run_scoped=run_scope,
            approve_item=approve_business,
            audit=audit,
        )

    assert exc.value.detail["code"] == SELF_APPROVAL
    assert "otra persona del equipo" in exc.value.detail["message"]
    assert order == ["begin", "guard", "rollback", f"audit:{DENIED_AUDIT_ACTION}"]


@pytest.mark.asyncio
async def test_distinct_approval_keeps_one_scope_and_transactional_audit() -> None:
    order: list[str] = []
    connection = object()

    async def run_scope(_pool, _user, work):
        order.append("begin")
        result = await work(connection, "tenant-a", "workspace-a")
        order.append("commit")
        return result

    async def approve_business(*_args, **kwargs):
        assert kwargs["decision_id"] == 42
        order.append("approve")
        return {"id": 8, "decision_id": 42, "ts": None}

    async def audit(**kwargs):
        assert kwargs["connection"] is connection
        order.append(f"audit:{kwargs['action']}")

    approval = await _approve(
        decision_id=42,
        run_scoped=run_scope,
        approve_item=approve_business,
        audit=audit,
    )

    assert approval.decision_id == 42
    assert order == ["begin", "approve", "audit:control_room.approve", "commit"]


@pytest.mark.asyncio
async def test_unexpected_errors_are_not_reported_as_denials() -> None:
    audit = AsyncMock()

    async def run_scope(_pool, _user, work):
        return await work(object(), "tenant-a", "workspace-a")

    with pytest.raises(RuntimeError, match="boom"):
        await _approve(
            decision_id=42,
            run_scoped=run_scope,
            approve_item=AsyncMock(side_effect=RuntimeError("boom")),
            audit=audit,
        )

    audit.assert_not_awaited()
