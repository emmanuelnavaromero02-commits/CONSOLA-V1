from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


WORKSPACE = "22222222-2222-4222-8222-222222222222"
USER = {
    "id": 7,
    "email": "u@example.com",
    "role": "admin",
    "active_workspace_id": WORKSPACE,
    "permissions": ["copilot.use"],
    "permission_set": {"copilot.use"},
}


def _client(copilot) -> TestClient:
    from starlette.middleware.base import BaseHTTPMiddleware

    from app.dependencies import require_authenticated
    from app.services.csrf import require_csrf

    class _InjectUser(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.user = USER
            return await call_next(request)

    api = FastAPI()
    api.add_middleware(_InjectUser)
    api.include_router(copilot.router)
    api.dependency_overrides[require_authenticated] = lambda: USER
    api.dependency_overrides[require_csrf] = lambda: None
    return TestClient(api)


def _rec(fingerprint: str, status: str, first_seen: str) -> dict:
    return {
        "fingerprint": fingerprint,
        "severity": "warning",
        "category": "pipeline",
        "title": "Revisar sincronización parcial",
        "body": "Últimos 7 días: 2 parciales.",
        "status": status,
        "action_href": "/viewer?type=pipeline",
        "first_seen_at": first_seen,
    }


@pytest.fixture()
def copilot(monkeypatch):
    from app.routers import copilot as module

    monkeypatch.setattr(
        module.proactive_service, "briefing_for_user", AsyncMock(return_value=[])
    )
    return module


def test_briefing_shows_only_active_cards_not_dismissed_in_this_episode(
    copilot, monkeypatch
):
    episode = "2026-10-05T12:00:00+00:00"
    monkeypatch.setattr(
        copilot.copilot_context_service,
        "list_recommendations",
        AsyncMock(
            return_value={
                "available": True,
                "recommendations": [
                    _rec("live:current", "active", episode),
                    _rec("live:old", "superseded", episode),
                    _rec("live:hidden", "active", episode),
                    _rec("live:recurred", "active", episode),
                ],
            }
        ),
    )
    dismissals = AsyncMock(
        return_value={
            f"live:hidden@{WORKSPACE}": datetime(2026, 10, 6, tzinfo=timezone.utc),
            f"live:recurred@{WORKSPACE}": datetime(2026, 9, 30, tzinfo=timezone.utc),
        }
    )
    monkeypatch.setattr(copilot.proactive_service, "live_dismissals", dismissals)

    response = _client(copilot).get("/api/copilot/briefing")

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["highlights"]] == [
        "live:current",
        "live:recurred",
    ]
    assert dismissals.await_args.args[0] == 7


def test_dismissing_a_live_card_is_bound_to_the_workspace(copilot, monkeypatch):
    live = AsyncMock(return_value=True)
    legacy = AsyncMock(return_value=True)
    monkeypatch.setattr(copilot.proactive_service, "dismiss_live_highlight", live)
    monkeypatch.setattr(copilot.proactive_service, "dismiss_highlight", legacy)
    monkeypatch.setattr(copilot.audit_service, "record_event", AsyncMock())

    client = _client(copilot)
    assert client.post("/api/copilot/briefing/live:abc/dismiss", json={}).status_code == 200
    assert client.post("/api/copilot/briefing/failures:x/dismiss", json={}).status_code == 200

    live.assert_awaited_once_with(7, f"live:abc@{WORKSPACE}")
    legacy.assert_awaited_once_with(7, "failures:x")


def test_live_copies_of_proactive_highlights_are_not_shown_twice(copilot, monkeypatch):
    episode = "2026-10-05T12:00:00+00:00"
    freshness = {
        "id": "freshness:replicon:never",
        "severity": "warning",
        "title": "replicon sin extracciones",
        "body": "Nunca se ejecutó una extracción exitosa para replicon.",
        "category": "freshness",
    }
    copy = {
        **_rec("live:copy", "active", episode),
        "category": "freshness",
        "title": freshness["title"],
        "evidence": '{"highlight": {"id": "freshness:replicon:never"}}',
    }
    pipeline = {**_rec("live:pipeline", "active", episode), "evidence": {"counts": {}}}
    monkeypatch.setattr(
        copilot.proactive_service, "briefing_for_user", AsyncMock(return_value=[freshness])
    )
    listing = AsyncMock(return_value={"available": True, "recommendations": [copy, pipeline]})
    monkeypatch.setattr(copilot.copilot_context_service, "list_recommendations", listing)
    monkeypatch.setattr(
        copilot.proactive_service, "live_dismissals", AsyncMock(return_value={})
    )

    response = _client(copilot).get("/api/copilot/briefing")

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["highlights"]] == [
        "live:pipeline",
        "freshness:replicon:never",
    ]
    assert copilot._briefing_copy_ids([{**copy, "evidence": {"highlight": {}}}]) == {
        "live:copy"
    }
    assert copilot._briefing_copy_ids([{**copy, "evidence": "not json"}]) == set()
