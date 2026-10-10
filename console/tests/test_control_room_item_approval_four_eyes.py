from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from app.services import control_room_service
from app.services.control_room import business_item_approval_guard as guard
from app.services.control_room.business_council_actors import SYSTEM_MAKER
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WorkflowStage,
    workflow_eligibility_provenance,
)
from test_control_room_permissions import _build_real_control_room_router_client


TENANT = "tenant-A"
WORKSPACE = "workspace-A"
ROUTES = (
    "/api/control-room/items/item-1/approve",
    "/api/control-room/anomalies/item-1/approve",
)
THRESHOLD = {
    "cartridge_id": "sap_hcm",
    "anomaly_type": "headcount_gap",
    "metric": "affected_people",
}


def _user(user_id: int, **overrides) -> dict:
    return {
        "id": user_id,
        "email": f"user-{user_id}@example.com",
        "role": "user",
        "workspace_role": "workspace_admin",
        "active_tenant_id": TENANT,
        "active_workspace_id": WORKSPACE,
        **overrides,
    }


def _item(**overrides) -> dict:
    return {
        "id": "item-1",
        "kind": "anomaly",
        "cartridge": "sap_hcm",
        "source_dataset": "employees_anomalies",
        "title": "Empleado terminado sigue activo",
        "recommendation": "Revisar baja.",
        "status": "decision_created",
        "decision_id": 91,
        "data_status": "ready",
        "metric_type": "count",
        "observed_value": 1,
        "population_count": 1,
        "observation_date": "2026-07-16",
        "evidence_refs": ["employees_anomalies:item-1"],
        "owner_user_id": 7,
        **overrides,
    }


class ApprovalDb:
    def __init__(
        self,
        *,
        created_by_id: int | None,
        created_by: str | None = None,
        authored_thresholds: bool = False,
    ) -> None:
        self.decision = {"id": 91, "created_by_id": created_by_id, "created_by": created_by}
        self.authored_thresholds = authored_thresholds
        self.action_inserted = False

    async def fetchrow(self, sql: str, *_args):
        if "FROM decisions" in sql:
            return dict(self.decision)
        if "item_id <> $3" in sql:
            return None
        if "FROM control_room_items" in sql:
            provenance = workflow_eligibility_provenance(
                _item(status="open", decision_id=None),
                stage=WorkflowStage.DECISION_CREATED,
                workspace_id=WORKSPACE,
                decision_id=91,
            )
            return {
                "item_id": "item-1",
                "decision_id": 91,
                "owner_user_id": 7,
                "item_kind": "anomaly",
                "status": "decision_created",
                "metadata": {DECISION_PROVENANCE_KEY: provenance},
            }
        if "INSERT INTO decision_actions" in sql:
            self.action_inserted = True
            return {"id": 3, "decision_id": 91, "ts": None}
        return None

    async def fetch(self, sql: str, *_args):
        if "FROM control_room_thresholds" in sql:
            return [{"id": "threshold-1", **THRESHOLD}]
        if "control_room.threshold.upsert" in sql:
            return [{"resource_id": "threshold-1"}] if self.authored_thresholds else []
        return []

    async def execute(self, sql: str, *_args):
        return "INSERT 0 1" if "INSERT INTO" in sql else "SELECT 1"


async def _scoped(pool, _user, work):
    return await work(pool, TENANT, WORKSPACE)


def _patched(stack: ExitStack, db: ApprovalDb, *, item: dict, current: bool = True):
    audit = AsyncMock()
    approve_link = AsyncMock()
    stack.enter_context(
        patch.object(
            control_room_service, "_item_for_mutation", AsyncMock(return_value=item)
        )
    )
    stack.enter_context(
        patch.object(control_room_service.auth, "pool", AsyncMock(return_value=db))
    )
    stack.enter_context(patch.object(control_room_service, "_run_with_db_scope", _scoped))
    stack.enter_context(patch.object(control_room_service, "_ensure_item_row", AsyncMock()))
    stack.enter_context(
        patch(
            "app.services.control_room.business_action_approval.lock_authoritative_business_item",
            AsyncMock(return_value={"item_id": "item-1", "status": "decision_created"}),
        )
    )
    stack.enter_context(
        patch.object(control_room_service, "link_control_room_decision", AsyncMock())
    )
    stack.enter_context(
        patch.object(control_room_service, "approve_control_room_decision", approve_link)
    )
    stack.enter_context(
        patch.object(control_room_service, "_lessons_for_item", return_value=[])
    )
    stack.enter_context(
        patch.object(
            control_room_service, "_impact_for_item", return_value={"confidence": 0.9}
        )
    )
    stack.enter_context(
        patch.object(
            control_room_service,
            "_project_public_item",
            side_effect=lambda item, _builder, **updates: {**item, **updates},
        )
    )
    stack.enter_context(
        patch.object(control_room_service.audit_service, "record_event", audit)
    )
    stack.enter_context(
        patch.object(
            guard, "actor_has_current_permission", AsyncMock(return_value=current)
        )
    )
    return audit, approve_link


def _post(user: dict, route: str):
    return _build_real_control_room_router_client(user).post(
        route,
        headers={"authorization": "Bearer test"},
        json={"decision_id": 91},
    )


def _assert_denied(response, audit, approve_link, db, *, reason: str, maker):
    assert response.status_code == 403, response.text
    assert response.json()["detail"] == {
        "code": reason,
        "message": guard.DENIAL_MESSAGES[reason],
    }
    assert db.action_inserted is False
    approve_link.assert_not_awaited()
    audit.assert_awaited_once()
    kwargs = audit.await_args.kwargs
    assert "connection" not in kwargs
    assert kwargs["action"] == guard.DENIED_AUDIT_ACTION
    assert kwargs["status"] == "denied"
    assert kwargs["resource_type"] == "control_room_item"
    assert kwargs["resource_id"] == "item-1"
    assert kwargs["metadata"] == {"reason": reason, "decision_id": 91, "maker": maker}


@pytest.mark.parametrize("route", ROUTES)
def test_decision_author_cannot_approve_own_decision(route: str) -> None:
    db = ApprovalDb(created_by_id=7)
    with ExitStack() as stack:
        audit, approve_link = _patched(stack, db, item=_item())
        response = _post(_user(7), route)

    _assert_denied(
        response, audit, approve_link, db, reason=guard.SELF_APPROVAL, maker="user:7"
    )
    assert "otra persona del equipo" in response.json()["detail"]["message"]


@pytest.mark.parametrize("route", ROUTES)
def test_distinct_approver_can_approve_person_decision(route: str) -> None:
    db = ApprovalDb(created_by_id=7)
    with ExitStack() as stack:
        audit, approve_link = _patched(stack, db, item=_item())
        response = _post(_user(9), route)

    assert response.status_code == 200, response.text
    assert response.json()["approved"] is True
    assert db.action_inserted is True
    approve_link.assert_awaited_once()
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["action"] == "control_room.approve"
    assert audit.await_args.kwargs["status"] == "success"


@pytest.mark.parametrize("route", ROUTES)
def test_system_made_decision_is_approvable_by_one_person_with_approve(
    route: str,
) -> None:
    db = ApprovalDb(created_by_id=None, created_by=SYSTEM_MAKER)
    with ExitStack() as stack:
        audit, approve_link = _patched(stack, db, item=_item())
        response = _post(_user(7), route)

    assert response.status_code == 200, response.text
    approve_link.assert_awaited_once()
    assert audit.await_args.kwargs["action"] == "control_room.approve"


def test_system_made_decision_rejects_checker_who_set_the_threshold() -> None:
    db = ApprovalDb(created_by_id=None, created_by=SYSTEM_MAKER, authored_thresholds=True)
    item = _item(thresholds_applied=[THRESHOLD])
    with ExitStack() as stack:
        audit, approve_link = _patched(stack, db, item=item)
        response = _post(_user(7), ROUTES[0])

    _assert_denied(
        response,
        audit,
        approve_link,
        db,
        reason=guard.CHECKER_INFLUENCED,
        maker=SYSTEM_MAKER,
    )


def test_decision_without_author_is_never_approvable() -> None:
    db = ApprovalDb(created_by_id=None, created_by=None)
    with ExitStack() as stack:
        audit, approve_link = _patched(stack, db, item=_item())
        response = _post(_user(9), ROUTES[0])

    _assert_denied(
        response, audit, approve_link, db, reason=guard.NO_AUTHOR, maker=None
    )


def test_writer_without_approve_permission_cannot_approve() -> None:
    db = ApprovalDb(created_by_id=7)
    platform_admin = _user(9, role="admin", workspace_role=None)
    with ExitStack() as stack:
        audit, approve_link = _patched(stack, db, item=_item())
        response = _post(platform_admin, ROUTES[0])

    _assert_denied(
        response,
        audit,
        approve_link,
        db,
        reason=guard.APPROVER_PERMISSION,
        maker="user:7",
    )


def test_revoked_approver_permission_is_rechecked_in_the_database() -> None:
    db = ApprovalDb(created_by_id=7)
    with ExitStack() as stack:
        audit, approve_link = _patched(stack, db, item=_item(), current=False)
        response = _post(_user(9), ROUTES[1])

    _assert_denied(
        response,
        audit,
        approve_link,
        db,
        reason=guard.APPROVER_PERMISSION,
        maker="user:7",
    )


def test_decision_maker_prefers_the_person_over_the_system_label() -> None:
    assert guard.decision_maker({"created_by_id": 7, "created_by": SYSTEM_MAKER}).label == "user:7"
    assert guard.decision_maker({"created_by_id": None, "created_by": SYSTEM_MAKER}).label == SYSTEM_MAKER
    assert guard.decision_maker({"created_by_id": None, "created_by": "ops@example.com"}) is None
    assert guard.decision_maker({"created_by_id": True}) is None


def test_every_denial_message_is_spanish_copy() -> None:
    assert set(guard.DENIAL_MESSAGES) == {
        guard.SELF_APPROVAL,
        guard.SELF_DISCARD,
        guard.IMPLICIT_SELF_APPROVAL,
        guard.NO_AUTHOR,
        guard.APPROVER_PERMISSION,
        guard.CHECKER_INFLUENCED,
    }
    for message in guard.DENIAL_MESSAGES.values():
        assert message.strip() and "approval" not in message.lower()


def test_council_commands_do_not_route_through_the_legacy_guard() -> None:
    from pathlib import Path

    from app.services.control_room import business_council_commands

    source = Path(business_council_commands.__file__).read_text(encoding="utf-8")
    assert "business_item_approval_guard" not in source
    assert "require_council_distinct_actors(maker, checker_id)" in source


def test_guard_and_council_share_one_coded_denial() -> None:
    from app.services.control_room import business_approval_denial as denial
    from app.services.control_room import business_council_commands

    assert guard.ItemApprovalDenied is denial.ItemApprovalDenied
    assert guard.DENIAL_MESSAGES is denial.DENIAL_MESSAGES
    assert business_council_commands.ItemApprovalDenied is denial.ItemApprovalDenied
    refused = denial.ItemApprovalDenied(denial.SELF_DISCARD, item_id="item-1")
    assert refused.status_code == 403
    assert refused.item_id == "item-1"
    assert refused.detail == {
        "code": "self_discard",
        "message": denial.DENIAL_MESSAGES[denial.SELF_DISCARD],
    }
