from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request

from app.dependencies import require_authenticated
from app.routers import control_room as routes
from app.routers import control_room_actions as actions_router
from app.schemas.control_room_direct_actions import (
    PROPOSAL_CREATED_MESSAGE,
    PROPOSAL_EXISTS_MESSAGE,
    DecisionProposalResponse,
    ExceptionApprovalResponse,
    ExceptionReopenResponse,
    StudioTargetResponse,
)
from app.services import control_room_service
from app.services.control_room import business_action_handle as handle_module
from app.services.control_room import business_decision_proposal as proposal
from app.services.control_room import business_studio_target as studio
from app.services.control_room.business_action_direct_contract import (
    direct_contract_from_persisted_row,
)
from app.services.control_room.business_action_handle import (
    ResolvedActionHandle,
    resolve_business_action_handle,
)
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_direct_action_authority import (
    DirectActionReplay,
    LockedDirectAction,
)
from app.services.csrf import require_csrf
from control_room_direct_action_fixtures import (
    STUDIO_ADMIN,
    authorization,
    direct_row,
)
from control_room_surface_fixtures import OPERATOR, VIEWER, business_item, snapshot


HANDLE = "f" * 64
TOKEN_ID = "33333333-3333-3333-3333-333333333333"
ROUTES = {
    "/api/control-room/actions/exception": ExceptionApprovalResponse,
    "/api/control-room/actions/exception-reopen": ExceptionReopenResponse,
    "/api/control-room/actions/decision-proposal": DecisionProposalResponse,
    "/api/control-room/actions/studio-target": StudioTargetResponse,
}
BODIES = {
    "/api/control-room/actions/exception": {
        "action_handle": HANDLE,
        "reason": "Proveedor validado por auditoría interna",
    },
    "/api/control-room/actions/exception-reopen": {
        "action_handle": HANDLE,
        "reason": "Revisar de nuevo",
    },
    "/api/control-room/actions/decision-proposal": {"action_handle": HANDLE},
    "/api/control-room/actions/studio-target": {"action_handle": HANDLE},
}


def _route(path: str):
    return next(route for route in routes.router.routes if route.path == path)


def _dependency_names(route) -> set[str]:
    names = set()
    for dependency in route.dependencies:
        call = dependency.dependency
        names.add(getattr(call, "required_permission", call.__name__))
    return names


def test_direct_action_routes_are_csrf_write_scoped_typed_posts_without_approve_paths():
    for path, model in ROUTES.items():
        route = _route(path)
        assert route.methods == {"POST"}
        assert route.response_model is model
        assert "/approve" not in path
        assert _dependency_names(route) == {"require_csrf", "control_room.write"}
        assert any(dep.dependency is require_csrf for dep in route.dependencies)


def _client(user: dict | None, *, csrf: bool = True) -> httpx.AsyncClient:
    app = FastAPI()
    if user is not None:

        @app.middleware("http")
        async def _inject(request: Request, call_next):
            request.state.user = user
            return await call_next(request)

        app.dependency_overrides[require_authenticated] = lambda: user
    if csrf:
        app.dependency_overrides[require_csrf] = lambda: None
    app.include_router(routes.router)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _service_patches():
    return (
        patch.object(
            actions_router,
            "approve_exception",
            new=AsyncMock(return_value=ExceptionApprovalResponse(action_handle=HANDLE)),
        ),
        patch.object(
            actions_router,
            "reopen_exception",
            new=AsyncMock(return_value=ExceptionReopenResponse(action_handle=HANDLE)),
        ),
        patch.object(
            actions_router,
            "create_decision_proposal",
            new=AsyncMock(
                return_value=DecisionProposalResponse(
                    action_handle=HANDLE,
                    status="proposal_created",
                    decision_id=41,
                    href="/decisions?tab=consejo&propuesta=41",
                    message=PROPOSAL_CREATED_MESSAGE,
                )
            ),
        ),
        patch.object(
            actions_router,
            "resolve_studio_target",
            new=AsyncMock(
                return_value=StudioTargetResponse(
                    action_handle=HANDLE, href="/studio?cartridge=sap_hcm&tab=capas"
                )
            ),
        ),
        patch.object(actions_router, "_control_room_cache_invalidate"),
    )


@pytest.mark.asyncio
async def test_readers_unauthenticated_and_missing_csrf_never_reach_services():
    patches = _service_patches()
    with patches[0] as a, patches[1] as b, patches[2] as c, patches[3] as d, patches[4]:
        for path, body in BODIES.items():
            async with _client(None) as client:
                assert (await client.post(path, json=body)).status_code == 401
            async with _client(VIEWER) as client:
                assert (await client.post(path, json=body)).status_code == 403
            async with _client(OPERATOR, csrf=False) as client:
                assert (await client.post(path, json=body)).status_code == 403
        for mock in (a, b, c, d):
            mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", sorted(BODIES))
async def test_bodies_are_strict_and_never_accept_item_or_template_ids(path):
    patches = _service_patches()
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        async with _client(OPERATOR) as client:
            for body in (
                {**BODIES[path], "item_id": "business-1"},
                {**BODIES[path], "template_id": "approve_exception"},
                {**BODIES[path], "action_handle": "not-a-handle"},
                {
                    key: value
                    for key, value in BODIES[path].items()
                    if key != "action_handle"
                },
            ):
                response = await client.post(path, json=body)
                assert response.status_code == 422, body


@pytest.mark.asyncio
async def test_writer_calls_each_service_once_and_invalidates_cache():
    patches = _service_patches()
    with (
        patches[0] as a,
        patches[1] as b,
        patches[2] as c,
        patches[3] as d,
        patches[4] as cache,
    ):
        async with _client(OPERATOR) as client:
            responses = {
                path: await client.post(path, json=body)
                for path, body in BODIES.items()
            }

    assert {path: response.status_code for path, response in responses.items()} == {
        path: 200 for path in BODIES
    }
    assert responses["/api/control-room/actions/exception"].json()["status"] == (
        "exception_approved"
    )
    assert responses["/api/control-room/actions/studio-target"].json()["href"] == (
        "/studio?cartridge=sap_hcm&tab=capas"
    )
    for mock in (a, b, c, d):
        mock.assert_awaited_once()
    assert a.await_args.kwargs["reason"] == "Proveedor validado por auditoría interna"
    assert cache.call_count == 3


class ProposalConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.committed = False

    @asynccontextmanager
    async def transaction(self):
        yield
        self.committed = True

    def is_in_transaction(self) -> bool:
        return False

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append((" ".join(query.split()), args))
        return "SELECT 1"

    async def fetchrow(self, query: str, *args: Any):
        statement = " ".join(query.split())
        self.calls.append((statement, args))
        if statement.startswith("UPDATE control_room_action_tokens"):
            return {"id": TOKEN_ID}
        if "SELECT decision_id" in statement:
            return {"decision_id": 57}
        return None


def _locked_proposal() -> LockedDirectAction:
    item = business_item()
    row = direct_row(item)
    contract = direct_contract_from_persisted_row(
        row,
        authorization=authorization(),
        template=ACTION_TEMPLATES["create_decision_proposal"],
        user=OPERATOR,
    )
    return LockedDirectAction(
        {"id": TOKEN_ID, "template_id": "create_decision_proposal"},
        contract,
        row,
        authorization(),
    )


@pytest.mark.asyncio
async def test_proposal_creates_decision_audits_and_consumes_in_one_transaction():
    conn = ProposalConn()
    locked = _locked_proposal()
    create = AsyncMock(return_value={"id": 57})
    audit = AsyncMock()
    with (
        patch.object(
            proposal, "find_direct_action_replay", new=AsyncMock(return_value=None)
        ),
        patch.object(
            proposal,
            "resolve_business_action_handle",
            new=AsyncMock(
                return_value=ResolvedActionHandle(
                    "business-1", "create_decision_proposal", HANDLE
                )
            ),
        ),
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value={"id": "business-1", "title": "Hallazgo"}),
        ),
        patch.object(
            control_room_service, "_create_and_link_business_decision", new=create
        ),
        patch.object(
            proposal, "lock_direct_action", new=AsyncMock(return_value=locked)
        ),
        patch.object(proposal.auth, "pool", new=AsyncMock(return_value=conn)),
        patch.object(proposal.audit_service, "record_event", new=audit),
    ):
        response = await proposal.create_decision_proposal(
            OPERATOR, action_handle=HANDLE, idempotency_key="k-1"
        )

    assert response.model_dump() == {
        "action_handle": HANDLE,
        "status": "proposal_created",
        "decision_id": 57,
        "href": "/decisions?tab=consejo&propuesta=57",
        "message": PROPOSAL_CREATED_MESSAGE,
    }
    create.assert_awaited_once()
    assert (
        create.await_args.kwargs["workspace_id"]
        == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    )
    assert audit.await_args.kwargs["connection"] is conn
    assert audit.await_args.kwargs["critical"] is True
    assert audit.await_args.kwargs["action"] == "control_room.decision.create"
    assert audit.await_args.kwargs["metadata"] == {
        "decision_id": 57,
        "template_id": "create_decision_proposal",
    }
    consume = [
        call
        for call in conn.calls
        if call[0].startswith("UPDATE control_room_action_tokens")
    ]
    assert len(consume) == 1
    assert consume[0][1][1] == "proposal_created"
    assert conn.committed is True


@pytest.mark.asyncio
async def test_proposal_rejects_a_mismatched_item_before_creating_anything():
    conn = ProposalConn()
    create = AsyncMock(side_effect=AssertionError("decision created"))
    with (
        patch.object(
            proposal, "find_direct_action_replay", new=AsyncMock(return_value=None)
        ),
        patch.object(
            proposal,
            "resolve_business_action_handle",
            new=AsyncMock(
                return_value=ResolvedActionHandle(
                    "business-1", "create_decision_proposal", HANDLE
                )
            ),
        ),
        patch.object(
            control_room_service,
            "_item_for_mutation",
            new=AsyncMock(return_value={"id": "business-2"}),
        ),
        patch.object(
            control_room_service, "_create_and_link_business_decision", new=create
        ),
        patch.object(
            proposal,
            "lock_direct_action",
            new=AsyncMock(return_value=_locked_proposal()),
        ),
        patch.object(proposal.auth, "pool", new=AsyncMock(return_value=conn)),
    ):
        with pytest.raises(HTTPException) as exc:
            await proposal.create_decision_proposal(OPERATOR, action_handle=HANDLE)

    assert exc.value.status_code == 409
    create.assert_not_awaited()
    assert conn.committed is False


@pytest.mark.asyncio
async def test_proposal_replay_reports_the_existing_decision_without_writes():
    conn = ProposalConn()
    resolve = AsyncMock(side_effect=AssertionError("resolved again"))
    with (
        patch.object(
            proposal,
            "find_direct_action_replay",
            new=AsyncMock(
                return_value=DirectActionReplay(
                    "business-1", "create_decision_proposal", "proposal_created"
                )
            ),
        ),
        patch.object(proposal, "resolve_business_action_handle", new=resolve),
        patch.object(proposal.auth, "pool", new=AsyncMock(return_value=conn)),
    ):
        response = await proposal.create_decision_proposal(
            OPERATOR, action_handle=HANDLE, idempotency_key="k-1"
        )

    assert response.status == "proposal_exists"
    assert response.decision_id == 57
    assert response.message == PROPOSAL_EXISTS_MESSAGE
    resolve.assert_not_awaited()
    assert not any(
        statement.startswith(("UPDATE", "INSERT")) for statement, _ in conn.calls
    )


@pytest.mark.asyncio
async def test_studio_target_resolves_only_studio_handles_and_writes_nothing():
    resolve = AsyncMock(
        return_value=ResolvedActionHandle(
            "business-1", "open_in_studio", HANDLE, cartridge_id="sap_hcm"
        )
    )
    with (
        patch.object(studio, "resolve_business_action_handle", new=resolve),
        patch.object(
            control_room_service.auth,
            "pool",
            new=AsyncMock(side_effect=AssertionError("db")),
        ),
    ):
        response = await studio.resolve_studio_target(
            STUDIO_ADMIN, action_handle=HANDLE
        )

    assert response.href == "/studio?cartridge=sap_hcm&tab=capas"
    assert resolve.await_args.kwargs["allowed_template_ids"] == frozenset(
        {"open_in_studio"}
    )
    for cartridge in (None, "", "sap-hcm", "x&tab=dags"):
        resolve.return_value = ResolvedActionHandle(
            "business-1", "open_in_studio", HANDLE, cartridge_id=cartridge
        )
        with patch.object(studio, "resolve_business_action_handle", new=resolve):
            with pytest.raises(HTTPException) as exc:
                await studio.resolve_studio_target(STUDIO_ADMIN, action_handle=HANDLE)
        assert exc.value.status_code == 404


class ResolveConn:
    def __init__(self, token: dict, row: dict | None) -> None:
        self.token = token
        self.row = row
        self.row_reads = 0

    async def execute(self, *_args: Any) -> str:
        return "SELECT 1"

    async def fetchrow(self, query: str, *_args: Any):
        if "FROM control_room_action_tokens" in query:
            return self.token
        return None

    async def fetch(self, query: str, *_args: Any):
        if "FROM control_room_items AS item" in query:
            self.row_reads += 1
            return [self.row] if self.row else []
        return []


def _token_for(contract) -> dict:
    return {
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


async def _resolve(user, conn, *, allowed=None, enabled=None, items=None):
    item = business_item()
    kwargs = {} if allowed is None else {"allowed_template_ids": allowed}
    with (
        patch.object(
            handle_module,
            "collect_surface_snapshot",
            new=AsyncMock(return_value=snapshot(items=items or (item,))),
        ),
        patch.object(
            handle_module,
            "load_enabled_action_template_ids",
            new=AsyncMock(
                return_value=enabled
                if enabled is not None
                else frozenset(
                    {"approve_exception", "open_in_studio", "create_followup_task"}
                )
            ),
        ),
        patch.object(handle_module.auth, "pool", new=AsyncMock(return_value=conn)),
        patch.object(
            handle_module,
            "capture_authorization_snapshot",
            new=AsyncMock(return_value=authorization(user)),
        ),
    ):
        return await resolve_business_action_handle(user, HANDLE, **kwargs)


def _approve_contract():
    item = business_item()
    return direct_contract_from_persisted_row(
        direct_row(item),
        authorization=authorization(),
        template=ACTION_TEMPLATES["approve_exception"],
        user=OPERATOR,
    )


@pytest.mark.asyncio
async def test_preview_resolution_still_rejects_every_direct_template():
    contract = _approve_contract()
    conn = ResolveConn(_token_for(contract), direct_row(business_item()))
    with pytest.raises(HTTPException) as exc:
        await _resolve(OPERATOR, conn)
    assert exc.value.status_code == 404
    assert conn.row_reads == 0


@pytest.mark.asyncio
async def test_direct_resolution_matches_template_enablement_item_and_digests():
    contract = _approve_contract()
    row = direct_row(business_item())
    resolved = await _resolve(
        OPERATOR,
        ResolveConn(_token_for(contract), row),
        allowed=frozenset({"approve_exception"}),
    )
    assert resolved.template_id == "approve_exception"
    assert resolved.item_id == "business-1"
    assert resolved.cartridge_id == "sap_hcm"
    assert "business-1" not in repr(resolved)

    failures = (
        {"allowed": frozenset({"create_decision_proposal"})},
        {"allowed": frozenset({"approve_exception"}), "enabled": frozenset()},
        {
            "allowed": frozenset({"approve_exception"}),
            "items": (business_item("other"),),
        },
        {"allowed": frozenset({"request_owner_review"})},
    )
    for kwargs in failures:
        with pytest.raises(HTTPException) as exc:
            await _resolve(OPERATOR, ResolveConn(_token_for(contract), row), **kwargs)
        assert exc.value.status_code == 404
    for tampered in (
        {**_token_for(contract), "binding_digest": "0" * 64},
        {**_token_for(contract), "target_digest": "0" * 64},
    ):
        with pytest.raises(HTTPException) as exc:
            await _resolve(
                OPERATOR,
                ResolveConn(tampered, row),
                allowed=frozenset({"approve_exception"}),
            )
        assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await _resolve(
            OPERATOR,
            ResolveConn(_token_for(contract), {**row, "status": "dismissed"}),
            allowed=frozenset({"approve_exception"}),
        )
    assert exc.value.status_code == 404


def test_response_models_reject_unsafe_links_and_statuses():
    for href in (
        "https://evil.example/decisions?tab=consejo&propuesta=1",
        "/decisions?tab=consejo&propuesta=0",
        "/decisions?tab=consejo&propuesta=1&x=2",
        "//evil.example",
    ):
        with pytest.raises(ValueError):
            DecisionProposalResponse(
                action_handle=HANDLE,
                status="proposal_created",
                decision_id=1,
                href=href,
                message=PROPOSAL_CREATED_MESSAGE,
            )
    for href in ("/studio?cartridge=SAP&tab=capas", "/studio?cartridge=x&tab=dags"):
        with pytest.raises(ValueError):
            StudioTargetResponse(action_handle=HANDLE, href=href)
    with pytest.raises(ValueError):
        ExceptionApprovalResponse(action_handle=HANDLE, status="dismissed")
    payload = json.loads(
        ExceptionApprovalResponse(action_handle=HANDLE).model_dump_json()
    )
    assert set(payload) == {"action_handle", "status", "reversible", "message"}
