from __future__ import annotations

import asyncio
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any, get_args, get_origin
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, Request

from app.dependencies import require_authenticated
from app.routers import control_room as routes
from app.schemas.control_room_council import (
    ActionCouncilResponse,
    CouncilEvidence,
    CouncilImpact,
    CouncilProposal,
)
from app.services.control_room import business_council_view as view
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
)
from control_room_direct_action_fixtures import direct_row
from control_room_get_edges import installed_read_edges
from control_room_get_harness import (
    TENANT_ID,
    WORKSPACE_ID,
    ConcurrencyProbe,
    MutationSentinel,
    build_app,
)
from control_room_surface_fixtures import business_item, snapshot


PATH = "/api/control-room/council"
MAKER = {
    "id": 9,
    "role": "tenant_admin",
    "email": "maker@example.test",
    "active_tenant_id": TENANT_ID,
    "active_workspace_id": WORKSPACE_ID,
    "allowed_cartridges": ["sap_hcm"],
}
CHECKER = {
    "id": 21,
    "role": "user",
    "workspace_role": "control_room_approver",
    "email": "checker@example.test",
    "active_tenant_id": TENANT_ID,
    "active_workspace_id": WORKSPACE_ID,
    "allowed_cartridges": ["sap_hcm"],
}
_WRITES = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|MERGE|CALL|TRUNCATE|CREATE|ALTER|DROP|COPY|LOCK)\b"
    r"|FOR\s+(?:UPDATE|SHARE|NO\s+KEY|KEY)|pg_advisory",
    re.I,
)
NOW = datetime.now(UTC)


def person_row(item_id: str = "proposal-1", **overrides: Any) -> dict[str, Any]:
    item = business_item(item_id, status="decision_created")
    row = direct_row(
        item,
        status="decision_created",
        decision_id=41,
        decision_workspace_id=WORKSPACE_ID,
        owner_user_id=9,
    )
    row.update(
        decision_status="open",
        decision_created_at=NOW - timedelta(hours=2),
        decision_commitment_date=date(2026, 10, 3),
        decision_created_by_id=9,
    )
    row.update(overrides)
    return row


def intent_for(row: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    intent = {
        "id": "11111111-1111-1111-1111-111111111111",
        "item_id": row["item_id"],
        "maker_user_id": 9,
        "checker_user_id": None,
        "state": "pending_approval",
        "expires_at": NOW + timedelta(hours=20),
        "created_at": NOW - timedelta(hours=1),
        "observation_fingerprint": row["metadata"][CURRENT_ELIGIBILITY_FINGERPRINT_KEY],
    }
    intent.update(overrides)
    return intent


FOLLOWUP_TEMPLATE = {
    "template_id": "create_followup_task",
    "cartridge_id": "platform",
    "label": "Crear seguimiento operativo",
    "requires_approval": True,
}


class CouncilConn:
    def __init__(
        self,
        persons: list[dict[str, Any]] | None = None,
        intents: list[dict[str, Any]] | None = None,
        direct: list[dict[str, Any]] | None = None,
        *,
        enabled: bool = True,
        thresholds: list[dict[str, Any]] | None = None,
        authored: list[str] | None = None,
    ) -> None:
        self.persons = persons or []
        self.intents = intents or []
        self.direct = direct or []
        self.enabled = enabled
        self.thresholds = thresholds or []
        self.authored = authored or []
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    def _record(self, query: str, args: tuple[Any, ...]) -> str:
        statement = " ".join(str(query).split())
        assert not _WRITES.search(statement), statement
        assert "control_room_action_tokens" not in statement
        self.statements.append((statement, args))
        return statement

    async def execute(self, query: str, *args: Any) -> str:
        self._record(query, args)
        return "SELECT 1"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        statement = self._record(query, args)
        if "decision.status = 'open'" in statement:
            statuses, council_only = set(args[2]), args[5]
            return [
                dict(row)
                for row in self.persons
                if row["status"] in statuses
                and (
                    not council_only
                    or (row["metadata"].get("writeback_result") or {}).get("origin")
                    == "control_room_council"
                )
            ][: args[4]]
        if "FROM control_room_action_intents" in statement:
            return [dict(row) for row in self.intents]
        if "LEFT JOIN decisions" in statement:
            wanted = set(args[2])
            return [dict(row) for row in self.direct if row["item_id"] in wanted]
        if "FROM control_room_action_templates" in statement:
            return [dict(FOLLOWUP_TEMPLATE)] if self.enabled else []
        if "FROM control_room_thresholds" in statement:
            return [dict(row) for row in self.thresholds]
        if "FROM audit_events" in statement:
            return [{"resource_id": value} for value in self.authored if value in args[1]]
        raise AssertionError(f"unexpected fetch: {statement}")


async def _council(
    user: dict[str, Any], conn: CouncilConn, *items: dict[str, Any]
) -> ActionCouncilResponse:
    with (
        patch.object(view.auth, "pool", new=AsyncMock(return_value=conn)),
        patch.object(
            view,
            "collect_surface_snapshot",
            new=AsyncMock(return_value=snapshot(items=items)),
        ),
    ):
        return await view.build_action_council(user)


@pytest.mark.asyncio
async def test_council_reads_only_inside_a_read_only_transaction():
    row = person_row()
    live = business_item("finding-7")
    conn = CouncilConn([row], [intent_for(row)], [direct_row(live)])
    council = await _council(CHECKER, conn, live)

    statements = [statement for statement, _args in conn.statements]
    assert statements[0].upper().startswith("SELECT SET_CONFIG")
    assert statements[1] == view.READ_ONLY_TRANSACTION_SQL
    assert all(
        statement.upper().startswith(("SELECT", "SET TRANSACTION READ ONLY"))
        for statement in statements
    )
    person_args = next(args for statement, args in conn.statements if "decision.status = 'open'" in statement)
    assert person_args[3] is None
    assert [proposal.origin for proposal in council.proposals] == ["person", "system"]


@pytest.mark.asyncio
async def test_checker_can_approve_while_the_maker_needs_another_person():
    row = person_row()
    conn = CouncilConn([row], [intent_for(row)])
    checker_view = (await _council(CHECKER, conn)).proposals[0]
    maker_view = (await _council(MAKER, CouncilConn([row], [intent_for(row)]))).proposals[0]

    assert checker_view.state == "pending_approval"
    assert checker_view.can_approve and checker_view.can_discard
    assert checker_view.disabled_reason is None
    assert checker_view.authored_by_you is False
    assert maker_view.state == "needs_other_approver"
    assert not maker_view.can_approve and not maker_view.can_discard
    assert maker_view.authored_by_you is True
    assert maker_view.disabled_reason == "Requiere la aprobación de otra persona del equipo."
    assert maker_view.proposal_id != checker_view.proposal_id
    assert re.fullmatch(r"[a-f0-9]{64}", checker_view.proposal_id)
    assert "proposal-1" not in checker_view.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent_overrides", "state", "reason", "maker_can_renew"),
    (
        (
            {"expires_at": NOW - timedelta(minutes=1)},
            "expired",
            "La propuesta venció; su autor puede renovarla.",
            True,
        ),
        (
            {"observation_fingerprint": "0" * 64},
            "source_changed",
            "Los datos de origen cambiaron desde que se preparó la propuesta.",
            True,
        ),
        (
            {"state": "stale"},
            "source_changed",
            "Los datos de origen cambiaron desde que se preparó la propuesta.",
            True,
        ),
        (
            None,
            "no_followup",
            "La tarea de seguimiento no está preparada; su autor puede renovarla.",
            True,
        ),
    ),
)
async def test_unapprovable_states_carry_their_reason(
    intent_overrides, state, reason, maker_can_renew
):
    row = person_row()
    intents = [] if intent_overrides is None else [intent_for(row, **intent_overrides)]
    checker_view = (await _council(CHECKER, CouncilConn([row], intents))).proposals[0]
    maker_view = (await _council(MAKER, CouncilConn([row], intents))).proposals[0]

    assert checker_view.state == maker_view.state == state
    assert checker_view.disabled_reason == reason
    assert not checker_view.can_approve and not checker_view.can_renew
    assert maker_view.can_renew is maker_can_renew


@pytest.mark.asyncio
async def test_completed_council_items_are_read_only_and_legacy_approvals_hidden():
    council_done = person_row(
        "proposal-done",
        status="approved",
        execution_status="executed",
        metadata={
            **person_row()["metadata"],
            "writeback_result": {"origin": "control_room_council"},
        },
    )
    legacy = person_row("proposal-legacy", status="approved", execution_status="executed")
    council = await _council(CHECKER, CouncilConn([council_done, legacy]))

    assert [proposal.state for proposal in council.proposals] == ["completed"]
    done = council.proposals[0]
    assert not (done.can_approve or done.can_discard or done.can_renew)
    assert done.disabled_reason is None


@pytest.mark.asyncio
async def test_system_suggestions_need_a_stored_matching_observation():
    live = business_item("finding-7")
    matched = await _council(CHECKER, CouncilConn(direct=[direct_row(live)]), live)
    missing = await _council(CHECKER, CouncilConn(), live)
    drifted = await _council(
        CHECKER,
        CouncilConn(
            direct=[
                direct_row(
                    live, metadata_updates={CURRENT_ELIGIBILITY_FINGERPRINT_KEY: "1" * 64}
                )
            ]
        ),
        live,
    )
    linked = await _council(
        CHECKER,
        CouncilConn(direct=[direct_row(live, decision_id=5, decision_workspace_id=WORKSPACE_ID)]),
        live,
    )
    admin = await _council(MAKER, CouncilConn(direct=[direct_row(live)]), live)
    analyst = {**MAKER, "id": 40, "role": "user", "workspace_role": "analyst"}
    reader = await _council(analyst, CouncilConn(direct=[direct_row(live)]), live)

    (suggestion,) = matched.proposals
    assert suggestion.origin == "system" and suggestion.decision_id is None
    assert suggestion.state == "pending_approval" and suggestion.can_approve
    assert missing.proposals == drifted.proposals == linked.proposals == []
    assert admin.proposals[0].proposal_id != matched.proposals[0].proposal_id
    (admin_view,) = admin.proposals
    assert admin_view.can_approve and admin_view.can_discard
    (reader_view,) = reader.proposals
    assert not reader_view.can_approve and not reader_view.can_discard
    assert reader_view.disabled_reason == "Requiere la aprobación de otra persona del equipo."


@pytest.mark.asyncio
async def test_restricted_writer_is_owner_scoped_and_sees_no_foreign_rows():
    restricted = {**MAKER, "role": "user", "workspace_role": "analyst", "id": 33}
    conn = CouncilConn()
    await _council(restricted, conn)
    person_args = next(args for statement, args in conn.statements if "decision.status = 'open'" in statement)
    assert person_args[3] == 33

    anonymous = {**restricted, "id": None}
    conn = CouncilConn()
    await _council(anonymous, conn)
    person_args = next(args for statement, args in conn.statements if "decision.status = 'open'" in statement)
    assert person_args[3] == 0


def test_council_models_are_strict_and_free_of_open_types():
    for model in (ActionCouncilResponse, CouncilProposal, CouncilImpact, CouncilEvidence):
        assert model.model_config.get("extra") == "forbid"
        for field in model.model_fields.values():
            nodes = [field.annotation, *get_args(field.annotation)]
            assert Any not in nodes
            assert all(get_origin(node) is not dict for node in nodes)


@pytest.mark.asyncio
async def test_asgi_council_is_repeatable_scoped_concurrent_and_dml_free():
    sentinel = MutationSentinel()
    before = sentinel.snapshot()
    probe = ConcurrencyProbe()
    app = build_app(probe)
    transport = httpx.ASGITransport(app=app)
    with installed_read_edges(sentinel, probe):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.get(PATH, headers={"x-purity-request": "council-a"})
            second = await client.get(PATH, headers={"x-purity-request": "council-b"})
            probe.reset()
            probe.enabled = True
            parallel = await asyncio.gather(
                client.get(PATH, headers={"x-purity-request": "council-c"}),
                client.get(PATH, headers={"x-purity-request": "council-d"}),
            )
            probe.enabled = False

    assert first.status_code == second.status_code == 200
    assert [response.status_code for response in parallel] == [200, 200]
    assert first.json()["proposals"] == second.json()["proposals"]
    assert parallel[0].json()["proposals"] == parallel[1].json()["proposals"]
    assert probe.max_in_flight == 2
    assert sentinel.snapshot() == before
    assert sentinel.mutation_attempts == []
    statements = [statement for _request, statement, _scope in sentinel.query_calls]
    assert view.READ_ONLY_TRANSACTION_SQL in statements
    assert not any("control_room_action_tokens" in s for s in statements)
    assert not any(re.search(r"FOR\s+UPDATE", s, re.I) for s in statements)


def _client(user: dict | None) -> httpx.AsyncClient:
    app = FastAPI()
    if user is not None:

        @app.middleware("http")
        async def _inject(request: Request, call_next):
            request.state.user = user
            return await call_next(request)

        app.dependency_overrides[require_authenticated] = lambda: user
    app.include_router(routes.router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_council_requires_authentication_and_dataset_read():
    build = AsyncMock(side_effect=AssertionError("council built"))
    with patch.object(view, "collect_surface_snapshot", build):
        async with _client(None) as client:
            assert (await client.get(PATH)).status_code == 401
        async with _client({**MAKER, "role": "workspace_user"}) as client:
            assert (await client.get(PATH)).status_code == 403
    build.assert_not_awaited()


def test_council_route_is_get_only_and_declares_dataset_read():
    matches = [
        route
        for route in routes.router.routes
        if route.path == PATH and "GET" in (route.methods or set())
    ]
    assert len(matches) == 1
    route = matches[0]
    permissions = {
        dependency.dependency.required_permission
        for dependency in route.dependencies
        if hasattr(dependency.dependency, "required_permission")
    }
    assert route.methods == {"GET"}
    assert permissions == {"datasets.read"}
    assert route.response_model is ActionCouncilResponse


@pytest.mark.asyncio
async def test_disabled_follow_ups_hide_suggestions_and_block_approval():
    row = person_row()
    live = business_item("finding-7")
    council = await _council(
        CHECKER,
        CouncilConn([row], [intent_for(row)], [direct_row(live)], enabled=False),
        live,
    )
    (proposal,) = council.proposals
    assert proposal.origin == "person" and not proposal.can_approve
    assert proposal.disabled_reason == (
        "Las tareas de seguimiento están desactivadas en este espacio de trabajo."
    )


@pytest.mark.asyncio
async def test_hidden_data_sources_hide_person_proposals():
    row = person_row()
    blind = {**CHECKER, "allowed_cartridges": []}
    council = await _council(blind, CouncilConn([row], [intent_for(row)]))
    assert council.proposals == []
    other = {**CHECKER, "allowed_cartridges": ["sap_b1"]}
    assert (await _council(other, CouncilConn([row], [intent_for(row)]))).proposals == []


def _threshold_item(item_id: str) -> dict[str, Any]:
    return business_item(
        item_id,
        thresholds_applied=[
            {
                "cartridge_id": "sap_hcm",
                "anomaly_type": "headcount_gap",
                "metric": "gap",
                "warning_value": 1,
                "source": "workspace",
            }
        ],
    )


@pytest.mark.asyncio
async def test_a_checker_who_set_the_threshold_cannot_approve_the_suggestion_alone():
    live = _threshold_item("finding-8")
    thresholds = [
        {"id": "31", "cartridge_id": "sap_hcm", "anomaly_type": "headcount_gap", "metric": "gap"}
    ]
    influenced = await _council(
        CHECKER,
        CouncilConn(direct=[direct_row(live)], thresholds=thresholds, authored=["31"]),
        live,
    )
    other = await _council(
        CHECKER,
        CouncilConn(direct=[direct_row(live)], thresholds=thresholds, authored=[]),
        live,
    )
    (blocked,) = influenced.proposals
    assert blocked.state == "needs_other_approver" and not blocked.can_approve
    assert blocked.can_discard is True
    assert blocked.disabled_reason.startswith("Ajustaste un umbral")
    (free,) = other.proposals
    assert free.can_approve and free.state == "pending_approval"


@pytest.mark.asyncio
async def test_completed_system_decisions_and_authorless_proposals_are_honest():
    done = person_row(
        "proposal-system",
        status="approved",
        execution_status="executed",
        decision_created_by_id=None,
        decision_created_by="system:control-room",
        metadata={
            **person_row()["metadata"],
            "writeback_result": {"origin": "control_room_council"},
        },
    )
    orphan = person_row("proposal-orphan", decision_created_by_id=None, decision_created_by=None)
    council = await _council(CHECKER, CouncilConn([done, orphan]))
    by_state = {proposal.state: proposal for proposal in council.proposals}
    assert by_state["completed"].origin == "system"
    assert by_state["completed"].decision_id == 41
    orphaned = by_state["no_followup"]
    assert orphaned.origin == "person"
    assert not (orphaned.can_discard or orphaned.can_renew or orphaned.can_approve)
    assert orphaned.disabled_reason == (
        "La propuesta no tiene un autor vigente; un administrador del espacio puede "
        "cerrarla desde el Registro."
    )


@pytest.mark.asyncio
async def test_latest_intent_query_keeps_one_row_per_item_and_maker_without_a_cap():
    row = person_row()
    conn = CouncilConn([row], [intent_for(row)])
    await _council(CHECKER, conn)
    statement, args = next(
        (statement, args)
        for statement, args in conn.statements
        if "FROM control_room_action_intents" in statement
    )
    assert "SELECT max(latest.created_at)" in statement
    assert "latest.maker_user_id = intent.maker_user_id" in statement
    assert "LIMIT" not in statement
    assert len(args) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent_overrides",
    ({"expires_at": NOW - timedelta(minutes=1)}, None, {"state": "stale"}),
)
async def test_disabled_follow_ups_never_promise_a_renewal(intent_overrides):
    row = person_row()
    intents = [] if intent_overrides is None else [intent_for(row, **intent_overrides)]
    for viewer in (MAKER, CHECKER):
        (proposal,) = (
            await _council(viewer, CouncilConn([row], intents, enabled=False))
        ).proposals
        assert not proposal.can_renew and not proposal.can_approve
        assert proposal.disabled_reason == (
            "Las tareas de seguimiento están desactivadas en este espacio de trabajo."
        )


@pytest.mark.asyncio
async def test_authorless_proposals_point_approvers_to_a_workspace_admin():
    orphan = person_row("proposal-orphan", decision_created_by_id=None, decision_created_by=None)
    (for_checker,) = (await _council(CHECKER, CouncilConn([orphan]))).proposals
    workspace_admin = {**MAKER, "workspace_role": "workspace_admin"}
    (for_admin,) = (await _council(workspace_admin, CouncilConn([orphan]))).proposals
    assigned = {**orphan, "decision_assignee_id": CHECKER["id"]}
    (for_assignee,) = (await _council(CHECKER, CouncilConn([assigned]))).proposals
    assert for_checker.disabled_reason == (
        "La propuesta no tiene un autor vigente; un administrador del espacio puede "
        "cerrarla desde el Registro."
    )
    assert for_admin.disabled_reason == for_assignee.disabled_reason == (
        "La propuesta no tiene un autor vigente; ciérrala desde el Registro."
    )


@pytest.mark.asyncio
async def test_default_source_thresholds_still_mark_the_author_as_influencer():
    live = business_item(
        "finding-9",
        thresholds_applied=[
            {
                "cartridge_id": "sap_hcm",
                "anomaly_type": "headcount_gap",
                "metric": "gap",
                "warning_value": 10,
                "source": "default",
            }
        ],
    )
    thresholds = [
        {"id": "44", "cartridge_id": "sap_hcm", "anomaly_type": "headcount_gap", "metric": "gap"}
    ]
    (proposal,) = (
        await _council(
            CHECKER,
            CouncilConn(direct=[direct_row(live)], thresholds=thresholds, authored=["44"]),
            live,
        )
    ).proposals
    assert proposal.state == "needs_other_approver" and not proposal.can_approve


@pytest.mark.asyncio
async def test_pending_proposals_are_not_crowded_out_by_completed_ones():
    done = [
        person_row(
            f"proposal-done-{index}",
            status="approved",
            execution_status="executed",
            decision_created_at=NOW - timedelta(minutes=index),
            metadata={
                **person_row()["metadata"],
                "writeback_result": {"origin": "control_room_council"},
            },
        )
        for index in range(15)
    ]
    old = person_row("proposal-old", decision_created_at=NOW - timedelta(days=30))
    conn = CouncilConn([*done, old], [intent_for(old)])
    council = await _council(CHECKER, conn)
    states = [proposal.state for proposal in council.proposals]
    assert states.count("completed") == 10
    assert "pending_approval" in states
    calls = [args for statement, args in conn.statements if "decision.status = 'open'" in statement]
    assert [(list(args[2]), args[4], args[5]) for args in calls] == [
        (["decision_created"], 100, False),
        (["approved"], 10, True),
    ]


@pytest.mark.asyncio
async def test_completed_system_decisions_show_when_the_finding_was_detected():
    first_seen = NOW - timedelta(days=3)
    done = person_row(
        "proposal-system",
        status="approved",
        execution_status="executed",
        first_seen_at=first_seen,
        decision_created_by_id=None,
        decision_created_by="system:control-room",
        metadata={
            **person_row()["metadata"],
            "writeback_result": {"origin": "control_room_council"},
        },
    )
    (proposal,) = (await _council(CHECKER, CouncilConn([done]))).proposals
    assert proposal.origin == "system" and proposal.created_at == first_seen

