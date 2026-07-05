from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.domains.agentops.invocation import (
    agent_invoke_background_requested,
    agent_invoke_background_response,
    agent_schedule_due,
    invoke_agent_payload,
    parse_agent_scheduled_fire_at,
)


class FakeAgents:
    def __init__(self, visible=True):
        self.visible = visible
        self.calls: list[tuple[str, dict]] = []

    async def get_agent(self, agent_id, *, user_context):
        self.calls.append((agent_id, user_context))
        return {"id": agent_id} if self.visible else None


class FakeRuntime:
    def __init__(self, agent=None):
        self.agent = agent
        self.runs: list[dict] = []

    async def load_agent(self, agent_id, *, user_context):
        return self.agent

    async def run(self, agent, message, *, history, user):
        self.runs.append(
            {"agent": agent, "message": message, "history": history, "user": user}
        )
        return {"reply": "ok"}


def test_agent_background_request_flags_and_response():
    assert agent_invoke_background_requested({"background": True})
    assert agent_invoke_background_requested({"queued": True})
    assert agent_invoke_background_requested({"async": True})
    assert agent_invoke_background_requested({"wait": False})
    assert not agent_invoke_background_requested({"wait": True})
    assert not agent_invoke_background_requested([])

    response = agent_invoke_background_response(SimpleNamespace(id="agent-1"))
    assert response["agent_id"] == "agent-1"
    assert response["queued"] is True
    assert response["status"] == "queued"


@pytest.mark.asyncio
async def test_invoke_agent_payload_runs_visible_agent():
    agent = SimpleNamespace(id="agent-1")
    runtime = FakeRuntime(agent=agent)

    result = await invoke_agent_payload(
        agent_id="agent-1",
        body={"message": " hola ", "history": [{"role": "user"}]},
        user={"id": 7},
        agents_service=FakeAgents(visible=True),
        agent_runtime=runtime,
        background_requested=lambda _body: False,
        start_background=lambda *_args: None,
        background_response=lambda _agent: {"queued": True},
    )

    assert result == {"reply": "ok"}
    assert runtime.runs == [
        {
            "agent": agent,
            "message": "hola",
            "history": [{"role": "user"}],
            "user": {"id": 7},
        }
    ]


@pytest.mark.asyncio
async def test_invoke_agent_payload_can_enqueue_background_run():
    agent = SimpleNamespace(id="agent-1")
    started: list[tuple] = []

    result = await invoke_agent_payload(
        agent_id="agent-1",
        body={"message": "run", "background": True},
        user={"id": 7},
        agents_service=FakeAgents(visible=True),
        agent_runtime=FakeRuntime(agent=agent),
        background_requested=lambda body: body.get("background") is True,
        start_background=lambda *args: started.append(args),
        background_response=lambda _agent: {"queued": True},
    )

    assert result == {"queued": True}
    assert started == [(agent, "run", [], {"id": 7})]


@pytest.mark.asyncio
async def test_invoke_agent_payload_rejects_missing_agent_or_message():
    with pytest.raises(HTTPException) as missing_exc:
        await invoke_agent_payload(
            agent_id="agent-1",
            body={"message": "run"},
            user={"id": 7},
            agents_service=FakeAgents(visible=False),
            agent_runtime=FakeRuntime(agent=SimpleNamespace(id="agent-1")),
            background_requested=lambda _body: False,
            start_background=lambda *_args: None,
            background_response=lambda _agent: {"queued": True},
        )

    assert missing_exc.value.status_code == 404

    with pytest.raises(HTTPException) as message_exc:
        await invoke_agent_payload(
            agent_id="agent-1",
            body={"message": "  "},
            user={"id": 7},
            agents_service=FakeAgents(visible=True),
            agent_runtime=FakeRuntime(agent=SimpleNamespace(id="agent-1")),
            background_requested=lambda _body: False,
            start_background=lambda *_args: None,
            background_response=lambda _agent: {"queued": True},
        )

    assert message_exc.value.status_code == 400


def test_agent_schedule_due_matches_explicit_fire_time():
    fire_at = parse_agent_scheduled_fire_at("2026-07-03T10:15:00Z")
    assert agent_schedule_due(
        {"cron": "15 10 * * *", "tz": "UTC"},
        scheduled_fire_at=fire_at,
        interval_minutes=5,
        grace_minutes=1,
    )
    assert not agent_schedule_due(
        {"cron": "20 10 * * *", "tz": "UTC"},
        scheduled_fire_at=fire_at,
        interval_minutes=5,
        grace_minutes=1,
    )


def test_agent_schedule_validation_errors_are_explicit():
    with pytest.raises(HTTPException) as exc:
        parse_agent_scheduled_fire_at("not-a-date")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as cron_exc:
        agent_schedule_due(
            {"cron": "not a cron"},
            scheduled_fire_at=parse_agent_scheduled_fire_at("2026-07-03T10:15:00Z"),
            interval_minutes=5,
            grace_minutes=1,
        )
    assert cron_exc.value.status_code == 403
