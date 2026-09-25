from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.routers import intelligence as router
from app.services import agent_runtime

TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
AGENT = "33333333-3333-4333-8333-333333333333"
HANDLE = "ab" * 16
PAYLOAD = {"wisdom_bit_id": "WB-FINANZAS", "signals": {"count": 2, "items": [{}, {}]}}


def _user(source: str = "agent_runner", agent_run_id: object = 41) -> dict:
    return {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "agent_id": AGENT,
        "agent_run_id": agent_run_id,
        "security_context_source": source,
    }


def _authority(tool: str = "wisdom_bits__run", run_id: int = 41) -> dict:
    agent = agent_runtime.Agent(
        id=AGENT,
        cartridge_id="sap_s4hana",
        slug="finance-monitor",
        name="Monitor de Finanzas",
        description="",
        instructions="",
        personality="",
        allowed_tools=[],
        rag_filter={},
        model="m",
        max_tokens=1,
        temperature=0.0,
        extra={},
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    )
    return agent_runtime._scheduled_effect_authority(
        agent=agent,
        run_id=run_id,
        tool=tool,
        args={"wisdom_bit_id": "WB-FINANZAS"},
        schedule_run_id=9,
        fencing_token=3,
    )


def _body(authority: dict | None) -> router.InternalMcpWisdomBitRequest:
    return router.InternalMcpWisdomBitRequest(
        security_context={},
        wisdom_bit_id="WB-FINANZAS",
        cartridge_id="sap_s4hana",
        effect_authority=authority,
    )


@pytest.fixture()
def mint(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    pool = object()
    monkeypatch.setattr(router.auth, "pool", AsyncMock(return_value=pool))
    mocked = AsyncMock(return_value=HANDLE)
    monkeypatch.setattr(router.evidence_tickets, "mint_monitor_evidence_ticket", mocked)
    mocked.pool = pool
    return mocked


@pytest.mark.asyncio
async def test_scheduled_monitor_with_verified_authority_gets_an_opaque_handle(mint):
    result = await router._with_monitor_evidence(
        _user(), _body(_authority()), "mcp-infra", dict(PAYLOAD)
    )

    assert result == {**PAYLOAD, "evidence_handle": HANDLE}
    mint.assert_awaited_once()
    args, kwargs = mint.await_args
    assert args == (mint.pool,)
    assert kwargs["schedule_run_id"] == 9
    assert kwargs["fencing_token"] == 3
    assert kwargs["agent_id"] == AGENT
    assert kwargs["tenant_id"] == TENANT
    assert kwargs["workspace_id"] == WORKSPACE
    assert kwargs["security_context_source"] == "agent_runner"
    assert kwargs["internal_service"] == "mcp-infra"
    assert kwargs["requested_wisdom_bit_id"] == "WB-FINANZAS"
    assert kwargs["requested_cartridge_id"] == "sap_s4hana"


@pytest.mark.asyncio
async def test_conversational_agent_stays_advisory_only_even_with_an_authority(mint):
    result = await router._with_monitor_evidence(
        _user(source="copilot"), _body(_authority()), "mcp-infra", dict(PAYLOAD)
    )

    assert result == PAYLOAD
    mint.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduled_caller_without_authority_gets_nothing(mint):
    result = await router._with_monitor_evidence(
        _user(), _body(None), "mcp-infra", dict(PAYLOAD)
    )

    assert result == PAYLOAD
    mint.assert_not_awaited()


@pytest.mark.asyncio
async def test_authority_for_another_tool_is_refused(mint):
    result = await router._with_monitor_evidence(
        _user(), _body(_authority(tool="decision__orchestrate")), "mcp-infra", dict(PAYLOAD)
    )

    assert result == PAYLOAD
    mint.assert_not_awaited()


@pytest.mark.asyncio
async def test_authority_for_another_run_is_refused(mint):
    result = await router._with_monitor_evidence(
        _user(agent_run_id=42), _body(_authority(run_id=41)), "mcp-infra", dict(PAYLOAD)
    )

    assert result == PAYLOAD
    mint.assert_not_awaited()


@pytest.mark.asyncio
async def test_tampered_authority_is_refused(mint):
    authority = {**_authority(), "fencing_token": 4}
    result = await router._with_monitor_evidence(
        _user(), _body(authority), "mcp-infra", dict(PAYLOAD)
    )

    assert result == PAYLOAD
    mint.assert_not_awaited()


@pytest.mark.asyncio
async def test_mint_refusal_returns_the_payload_untouched(mint):
    mint.return_value = None
    result = await router._with_monitor_evidence(
        _user(), _body(_authority()), "mcp-infra", dict(PAYLOAD)
    )

    assert result == PAYLOAD


@pytest.mark.asyncio
async def test_pool_failure_never_breaks_the_monitor(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        router.auth, "pool", AsyncMock(side_effect=RuntimeError("db unavailable"))
    )
    result = await router._with_monitor_evidence(
        _user(), _body(_authority()), "mcp-infra", dict(PAYLOAD)
    )

    assert result == PAYLOAD


def test_request_model_accepts_the_forwarded_authority_and_defaults_to_none():
    assert _body(None).effect_authority is None
    body = router.InternalMcpWisdomBitRequest(
        security_context={}, wisdom_bit_id="WB-TALENTO"
    )
    assert body.effect_authority is None
