from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.domains.agentops.invocation import (
    agent_invoke_background_requested,
    agent_invoke_background_response,
    agent_schedule_due,
    parse_agent_scheduled_fire_at,
)


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
