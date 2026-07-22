from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service
from app.services.control_room.business_approve_with_optional_decision import (
    approve_with_optional_decision,
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
        "status": "open",
    }


def test_empty_approve_body_remains_compatible() -> None:
    payload = {
        "approved": True,
        "decision_id": 42,
        "action": {"id": 8},
        "item": {"id": "item-1", "status": "approved"},
        "anomaly": {"id": "item-1", "status": "approved"},
    }
    approve = AsyncMock(return_value=payload)
    with patch.object(control_room_service, "approve_item", approve):
        response = _build_real_control_room_router_client(USER).post(
            "/api/control-room/items/item-1/approve",
            headers={"authorization": "Bearer test"},
            content=b"",
        )

    assert response.status_code == 200
    assert response.json()["decision_id"] == 42
    approve.assert_awaited_once()
    assert approve.await_args.kwargs["decision_id"] is None


@pytest.mark.asyncio
async def test_implicit_approval_has_one_scope_and_audits_after_commit() -> None:
    order: list[str] = []
    scope_calls = 0

    class Connection:
        async def fetchrow(self, sql: str, *_args):
            assert "FROM control_room_items" in sql
            order.append("reconstruct")
            return {
                "tenant_id": USER["active_tenant_id"],
                "workspace_id": USER["active_workspace_id"],
                "owner_user_id": 7,
                "item_id": "item-1",
                "status": "decision_created",
                "decision_id": 42,
                "selected_option_id": None,
                "execution_status": "not_started",
                "metadata": {},
            }

    async def run_scope(_pool, _user, work):
        nonlocal scope_calls
        scope_calls += 1
        order.append("begin")
        result = await work(
            Connection(),
            USER["active_tenant_id"],
            USER["active_workspace_id"],
        )
        order.append("commit")
        return result

    async def create_and_link(*_args, **_kwargs):
        order.append("create")
        return {"id": 42}

    async def approve_business(*_args, **kwargs):
        assert kwargs["item"]["decision_id"] == 42
        assert kwargs["item"]["status"] == "decision_created"
        order.append("approve")
        return {"id": 8, "decision_id": 42, "ts": None}

    async def audit(**kwargs):
        assert "commit" in order
        order.append(f"audit:{kwargs['action']}")

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
        patch.object(
            control_room_service,
            "_create_and_link_business_decision",
            create_and_link,
        ),
        patch.object(control_room_service, "_approve_business_item", approve_business),
        patch.object(control_room_service, "_lessons_for_item", return_value=["rule"]),
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
        result = await control_room_service.approve_item("item-1", USER)

    assert result["approved"] is True
    assert scope_calls == 1
    assert order == [
        "begin",
        "create",
        "reconstruct",
        "approve",
        "commit",
        "audit:control_room.decision.create",
        "audit:control_room.approve",
    ]


@pytest.mark.asyncio
async def test_post_link_failpoint_aborts_the_single_scope() -> None:
    order: list[str] = []

    async def run_scope(_pool, _user, work):
        order.append("begin")
        try:
            return await work(object(), "tenant-a", "workspace-a")
        except Exception:
            order.append("rollback")
            raise

    async def create_and_link(*_args, **_kwargs):
        order.append("link")
        return {"id": 42}

    async def fail(_conn, _decision_id):
        order.append("failpoint")
        raise RuntimeError("post-link")

    with pytest.raises(RuntimeError, match="post-link"):
        await approve_with_optional_decision(
            object(),
            user={"id": 7},
            item={"id": "item-1"},
            decision_id=None,
            lessons=("rule",),
            confidence=0.9,
            run_scoped=run_scope,
            ensure_item_row=AsyncMock(),
            record_item_event=AsyncMock(),
            create_and_link=create_and_link,
            approve_item=AsyncMock(),
            link_decision=AsyncMock(),
            approve_link=AsyncMock(),
            post_link_hook=fail,
        )

    assert order == ["begin", "link", "failpoint", "rollback"]
