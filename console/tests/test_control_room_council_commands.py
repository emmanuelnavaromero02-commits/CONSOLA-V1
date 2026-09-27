from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from app.dependencies import require_authenticated
from app.domains.decisions.access import can_view_decisions, require_decisions_page
from app.domains.iam.access_payload import access_ui_capabilities
from app.routers import control_room as routes
from app.schemas.control_room_council import (
    CouncilApproveRequest,
    CouncilApproveResponse,
    CouncilDiscardRequest,
    CouncilImpact,
    CouncilProposal,
)
from app.schemas.control_room_surfaces import ExperienceMetric
from app.services.control_room import business_council_commands as commands
from app.services.control_room.business_action_followup_effect import (
    complete_followup_effect,
)
from app.services.control_room.business_council_actors import (
    SYSTEM_MAKER,
    CouncilMaker,
    require_council_checker,
    require_council_distinct_actors,
)
from app.services.control_room.business_council_impact import council_impact
from app.services.permissions import get_effective_permissions
from control_room_surface_fixtures import TENANT_ID, WORKSPACE_ID, snapshot


HANDLE = "c" * 64
CHECKER = {
    "id": 21,
    "role": "user",
    "workspace_role": "control_room_approver",
    "email": "checker@example.test",
    "active_tenant_id": TENANT_ID,
    "active_workspace_id": WORKSPACE_ID,
}
WRITER = {**CHECKER, "id": 9, "role": "tenant_admin", "workspace_role": None}
PLATFORM = {**CHECKER, "id": 1, "role": "super_admin", "workspace_role": None}


def test_system_maker_is_distinct_from_any_human_checker():
    require_council_distinct_actors(CouncilMaker.system(), 21)
    require_council_distinct_actors(CouncilMaker.person(9), 21)
    assert CouncilMaker.system().label == SYSTEM_MAKER == "system:control-room"
    assert CouncilMaker.person(9).label == "user:9"


@pytest.mark.parametrize(
    ("maker", "checker"),
    (
        (CouncilMaker.person(21), 21),
        (CouncilMaker("system", 21), 21),
        (CouncilMaker("system", 5), 21),
        (CouncilMaker("person", None), 21),
        (CouncilMaker.system(), 0),
        (CouncilMaker.system(), True),
    ),
)
def test_four_eyes_rejects_same_or_malformed_actors(maker, checker):
    with pytest.raises(HTTPException) as denied:
        require_council_distinct_actors(maker, checker)
    assert denied.value.status_code == 403


@pytest.mark.parametrize("value", (None, 0, -3, True, "abc"))
def test_person_maker_requires_a_real_user(value):
    with pytest.raises(HTTPException):
        CouncilMaker.person(value)


def test_only_approvers_with_execute_are_council_checkers():
    assert require_council_checker(CHECKER) == 21
    for user in (WRITER, PLATFORM, {**CHECKER, "workspace_role": "viewer"}):
        with pytest.raises(HTTPException) as denied:
            require_council_checker(user)
        assert denied.value.status_code == 403


@pytest.mark.parametrize(
    ("user", "expected"),
    (
        ({"id": 1, "role": "admin"}, True),
        ({"id": 1, "role": "super_admin"}, True),
        (CHECKER, True),
        (WRITER, True),
        ({"id": 3, "role": "analyst"}, False),
        ({"id": 3, "role": "viewer"}, False),
        ({"id": 3, "role": "user", "workspace_role": "viewer"}, False),
        (None, False),
    ),
)
def test_decisions_are_visible_to_platform_admins_approvers_and_writers(user, expected):
    assert can_view_decisions(user) is expected


def test_decisions_page_guard_uses_the_same_rule():
    class _State:
        user: Any = None

    class _Request:
        state = _State()

    request = _Request()
    with pytest.raises(HTTPException) as missing:
        require_decisions_page(request)  # type: ignore[arg-type]
    assert missing.value.status_code == 401
    request.state.user = {"id": 3, "role": "analyst"}
    with pytest.raises(HTTPException) as denied:
        require_decisions_page(request)  # type: ignore[arg-type]
    assert denied.value.status_code == 403
    request.state.user = CHECKER
    assert require_decisions_page(request) == CHECKER  # type: ignore[arg-type]


def test_access_payload_exposes_decisions_to_the_council_roles():
    approver = access_ui_capabilities(
        get_effective_permissions(CHECKER),
        role_canonical="user",
        workspace_role_resolved="control_room_approver",
    )
    analyst = access_ui_capabilities(
        get_effective_permissions({"role": "analyst"}),
        role_canonical="analyst",
        workspace_role_resolved=None,
    )
    assert approver["can_view_decisions"] is True
    assert analyst["can_view_decisions"] is False


def test_money_impact_always_carries_its_formula_and_basis():
    persisted = council_impact(
        {"impact_estimate": 4200, "impact_currency": "MXN", "cartridge": "sap_hcm"}, None
    )
    assert (persisted.kind, persisted.value, persisted.currency) == ("money", 4200.0, "MXN")
    assert persisted.basis == "persisted"
    assert persisted.formula == "Impacto persistido en control_room_items."
    assert persisted.label == "Estimación registrada con el hallazgo"

    rule = council_impact(
        {
            "cartridge": "replicon",
            "anomaly_type": "low_margin",
            "details": {
                "financial_status": "ready",
                "base_currency": "USD",
                "original_currency": "USD",
                "revenue_usd": 10000,
                "margen_bruto_usd": 1000,
            },
        },
        None,
    )
    assert rule.kind == "money" and rule.basis == "rule"
    assert rule.value == pytest.approx(1000.0)
    assert rule.label == "Regla: brecha vs margen objetivo 20 % más WIP en revisión"
    assert rule.formula.startswith("max(0, revenue_usd * 20%")


def test_time_impact_only_from_hour_metrics_and_otherwise_no_estimate():
    hours = ExperienceMetric(name="Horas extra", kind="amount", value=12.5, unit="horas")
    count = ExperienceMetric(name="Personas", kind="count", value=4.0, unit=None)
    base = {"cartridge": "sap_hcm", "anomaly_type": "missing_manager", "details": {}}

    time = council_impact(base, hours)
    assert (time.kind, time.value, time.unit, time.basis) == ("time", 12.5, "horas", "observed")
    assert time.formula
    for metric in (count, None):
        none = council_impact(base, metric)
        assert none.kind == "none" and none.label == "Sin estimación"
        assert none.value is None and none.formula is None


@pytest.mark.parametrize(
    "payload",
    (
        {"kind": "none", "label": "Sin estimación", "value": 1.0},
        {"kind": "none", "label": "Aproximadamente mucho"},
        {"kind": "money", "label": "x", "value": 1.0, "basis": "rule"},
        {"kind": "money", "label": "x", "value": 1.0, "basis": "rule", "formula": "f"},
        {"kind": "time", "label": "x", "value": 1.0, "basis": "observed", "formula": "f"},
    ),
)
def test_impact_model_rejects_invented_or_unexplained_figures(payload):
    with pytest.raises(ValidationError):
        CouncilImpact.model_validate(payload)


def test_proposal_model_keeps_capabilities_consistent():
    base = {
        "proposal_id": HANDLE,
        "origin": "person",
        "authored_by_you": False,
        "decision_id": 3,
        "title": "Propuesta",
        "severity": "high",
        "state": "pending_approval",
        "impact": {"kind": "none", "label": "Sin estimación"},
        "can_approve": True,
        "can_discard": True,
        "can_renew": False,
    }
    CouncilProposal.model_validate(base)
    for invalid in (
        {**base, "disabled_reason": "Requiere la aprobación de otra persona del equipo."},
        {**base, "origin": "system"},
        {**base, "decision_id": None},
        {**base, "state": "completed"},
        {**base, "proposal_id": "item-1"},
        {**base, "item_id": "item-1"},
        {**base, "disabled_reason": "Texto libre", "can_approve": False},
    ):
        with pytest.raises(ValidationError):
            CouncilProposal.model_validate(invalid)


@pytest.mark.parametrize(
    "reason",
    (
        "corto",
        "- - - - - - - - - - - -",
        "Motivo​ con un carácter invisible",
        "Motivo ㅤㅤ con relleno en blanco",
        "Motivo\ncon salto de línea",
        None,
    ),
)
def test_discard_requires_a_visible_reason(reason):
    with pytest.raises(ValidationError):
        CouncilDiscardRequest.model_validate({"reason": reason, "idempotency_key": "key-12345"})


def test_command_bodies_are_strict():
    request = CouncilDiscardRequest.model_validate(
        {"reason": "  La causa se corrigió en origen  ", "idempotency_key": " key-12345 "}
    )
    assert request.reason == "La causa se corrigió en origen"
    assert request.idempotency_key == "key-12345"
    for invalid in (
        {"idempotency_key": "short", "confirm": True},
        {"idempotency_key": "key-12345", "confirm": False},
        {"idempotency_key": "key-12345"},
        {"idempotency_key": "key-12345", "confirm": True, "item_id": "x"},
    ):
        with pytest.raises(ValidationError):
            CouncilApproveRequest.model_validate(invalid)
    response = CouncilApproveResponse(decision_id=4)
    assert "No se modificó ningún sistema externo (ERP)" in response.message


def _route(path: str, method: str = "POST"):
    matches = [
        route
        for route in routes.router.routes
        if route.path == path and method in (route.methods or set())
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize(
    ("command", "permission"),
    (("approve", "control_room.approve"), ("discard", "control_room.approve"), ("renew", "control_room.write")),
)
def test_council_commands_require_csrf_and_their_permission(command, permission):
    route = _route(f"/api/control-room/council/{{proposal_id}}/{command}")
    names = {getattr(dep.dependency, "__name__", "") for dep in route.dependencies}
    permissions = {
        dep.dependency.required_permission
        for dep in route.dependencies
        if hasattr(dep.dependency, "required_permission")
    }
    assert "require_csrf" in names
    assert permissions == {permission}
    assert route.response_model is not None


def _client(user: dict) -> httpx.AsyncClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    app.dependency_overrides[require_authenticated] = lambda: user
    app.include_router(routes.router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_non_opaque_proposal_ids_never_reach_the_service():
    approve = AsyncMock(side_effect=AssertionError("service reached"))
    with (
        patch("app.routers.control_room_council.approve_council_proposal", approve),
        patch("app.routers.control_room_council.require_csrf", lambda: None),
    ):
        async with _client(CHECKER) as client:
            response = await client.post(
                "/api/control-room/council/employees:1001/approve",
                json={"idempotency_key": "key-12345", "confirm": True},
            )
    assert response.status_code in {403, 404, 422}
    approve.assert_not_awaited()


@pytest.mark.asyncio
async def test_writer_cannot_approve_before_touching_the_database():
    pool = AsyncMock(side_effect=AssertionError("database touched"))
    with patch.object(commands.auth, "pool", pool):
        with pytest.raises(HTTPException) as denied:
            await commands.approve_council_proposal(
                WRITER, HANDLE, idempotency_key="key-12345"
            )
    assert denied.value.status_code == 403
    pool.assert_not_awaited()


def test_replay_keys_are_bound_to_operation_actor_key_and_proposal():
    base = dict(workspace_id=WORKSPACE_ID, actor=21, operation="approve", key="k-1234567", proposal_id=HANDLE)
    reference = commands.replay_keys(**base)
    for change in (
        {"operation": "discard"},
        {"actor": 22},
        {"key": "k-7654321"},
        {"workspace_id": TENANT_ID},
    ):
        assert commands.replay_keys(**{**base, **change}).idempotency_digest != (
            reference.idempotency_digest
        )
    assert commands.replay_keys(**{**base, "proposal_id": "d" * 64}).proposal_digest != (
        reference.proposal_digest
    )
    assert "k-1234567" not in repr(reference)


class ReplayConn:
    def __init__(self, recorded: dict[str, Any] | None) -> None:
        self.recorded = recorded
        self.statements: list[str] = []

    async def execute(self, query: str, *_args: Any) -> str:
        self.statements.append(" ".join(query.split()))
        return "SELECT 1"

    async def fetchrow(self, query: str, *_args: Any) -> dict[str, Any] | None:
        self.statements.append(" ".join(query.split()))
        return self.recorded


@pytest.mark.asyncio
async def test_idempotency_replays_only_the_same_proposal_and_operation():
    keys = commands.replay_keys(
        workspace_id=WORKSPACE_ID, actor=21, operation="approve", key="k-1234567", proposal_id=HANDLE
    )
    same = ReplayConn(
        {"event_type": "action_executed", "metadata": {**keys.metadata, "decision_id": 8}}
    )
    outcome = await commands._replayed(
        same, workspace_id=WORKSPACE_ID, actor=21, keys=keys, event_type="action_executed"
    )
    assert outcome == commands.Outcome("done", 8)
    assert same.statements[0].startswith("SELECT pg_advisory_xact_lock")

    other = ReplayConn(
        {
            "event_type": "action_executed",
            "metadata": {**keys.metadata, "council_proposal_digest": "0" * 64},
        }
    )
    with pytest.raises(HTTPException) as reused:
        await commands._replayed(
            other, workspace_id=WORKSPACE_ID, actor=21, keys=keys, event_type="action_executed"
        )
    assert reused.value.status_code == 409
    assert (
        await commands._replayed(
            ReplayConn(None), workspace_id=WORKSPACE_ID, actor=21, keys=keys, event_type="action_executed"
        )
        is None
    )


class EmptyConn:
    async def execute(self, *_args: Any) -> str:
        return "SELECT 1"

    async def fetch(self, *_args: Any) -> list[dict[str, Any]]:
        return []

    async def fetchrow(self, *_args: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_unknown_proposal_is_not_found_after_both_lookups():
    with (
        patch.object(commands.auth, "pool", new=AsyncMock(return_value=EmptyConn())),
        patch.object(
            commands,
            "collect_surface_snapshot",
            new=AsyncMock(return_value=snapshot(items=())),
        ),
    ):
        with pytest.raises(HTTPException) as missing:
            await commands.approve_council_proposal(CHECKER, HANDLE, idempotency_key="key-12345")
        with pytest.raises(HTTPException) as missing_discard:
            await commands.discard_council_proposal(
                CHECKER, HANDLE, reason="Motivo suficiente", idempotency_key="key-12345"
            )
    assert missing.value.status_code == missing_discard.value.status_code == 404


class GuardConn:
    def __init__(self) -> None:
        self.writes: list[str] = []

    async def fetchrow(self, query: str, *_args: Any) -> dict[str, Any]:
        self.writes.append(query)
        return {"id": 3, "status": "open"}

    async def execute(self, query: str, *_args: Any) -> str:
        self.writes.append(query)
        return "UPDATE 1"

    async def fetchval(self, query: str, *_args: Any) -> int:
        self.writes.append(query)
        return 1


ROW = {
    "workspace_id": WORKSPACE_ID,
    "item_id": "item-1",
    "owner_user_id": 9,
    "status": "decision_created",
    "decision_id": 3,
    "execution_status": "not_started",
    "metadata": {},
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "maker", "status"),
    (
        ({**ROW, "status": "approved"}, CouncilMaker.person(9), 409),
        ({**ROW, "decision_id": 4}, CouncilMaker.person(9), 409),
        ({**ROW, "execution_status": "executed"}, CouncilMaker.person(9), 409),
        (ROW, CouncilMaker.person(21), 403),
    ),
)
async def test_effect_refuses_wrong_stage_or_same_actor_before_writing(row, maker, status):
    conn = GuardConn()
    with pytest.raises(HTTPException) as refused:
        await complete_followup_effect(
            conn, checker=CHECKER, row=row, item={"title": "t"}, decision_id=3, maker=maker
        )
    assert refused.value.status_code == status
    assert conn.writes == []
