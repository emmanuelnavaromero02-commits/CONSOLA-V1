from __future__ import annotations

import asyncio
from urllib.parse import quote

import httpx
import pytest

from app.routers import control_room as routes
from control_room_get_edges import installed_read_edges
from control_room_get_harness import (
    ITEM_ID,
    TENANT_ID,
    WORKSPACE_ID,
    ConcurrencyProbe,
    MutationSentinel,
    build_app,
)


ENCODED_ITEM_ID = quote(ITEM_ID, safe="")
GET_PATHS = {
    "/api/control-room/summary": "/api/control-room/summary",
    "/api/control-room/dashboard": "/api/control-room/dashboard",
    "/api/control-room/sap-successfactors/gold-kpis": (
        "/api/control-room/sap-successfactors/gold-kpis"
    ),
    "/api/control-room/sap-successfactors/talent-kpis": (
        "/api/control-room/sap-successfactors/talent-kpis"
    ),
    "/api/control-room/sap-successfactors/talent/overview": (
        "/api/control-room/sap-successfactors/talent/overview"
    ),
    "/api/control-room/sap-successfactors/talent/9box": (
        "/api/control-room/sap-successfactors/talent/9box"
    ),
    "/api/control-room/sap-successfactors/talent/9box/{box_id}": (
        "/api/control-room/sap-successfactors/talent/9box/core"
    ),
    "/api/control-room/sap-successfactors/talent/anomalies": (
        "/api/control-room/sap-successfactors/talent/anomalies"
    ),
    "/api/control-room/sap-successfactors/talent/metadata-readiness": (
        "/api/control-room/sap-successfactors/talent/metadata-readiness"
    ),
    "/api/control-room/banxico/readiness": "/api/control-room/banxico/readiness",
    "/api/control-room/inegi/readiness": "/api/control-room/inegi/readiness",
    "/api/control-room/sec-edgar/readiness": ("/api/control-room/sec-edgar/readiness"),
    "/api/control-room/sap-successfactors/market-validation": (
        "/api/control-room/sap-successfactors/market-validation"
    ),
    "/api/control-room/ops/summary": "/api/control-room/ops/summary",
    "/api/control-room/agents/ops": "/api/control-room/agents/ops?limit=12",
    "/api/control-room/decision-intelligence/runs": (
        "/api/control-room/decision-intelligence/runs?limit=50"
    ),
    "/api/control-room/decision-intelligence/runs/{run_id}": (
        "/api/control-room/decision-intelligence/runs/run-1"
    ),
    "/api/control-room/decision-intelligence/history": (
        "/api/control-room/decision-intelligence/history?limit=100"
    ),
    "/api/control-room/decision-intelligence/calibration": (
        "/api/control-room/decision-intelligence/calibration?min_outcomes_required=10"
    ),
    "/api/control-room/alerts": "/api/control-room/alerts",
    "/api/control-room/anomalies": "/api/control-room/anomalies",
    "/api/control-room/items/{item_id}": (f"/api/control-room/items/{ENCODED_ITEM_ID}"),
    "/api/control-room/items/{item_id}/impact": (
        f"/api/control-room/items/{ENCODED_ITEM_ID}/impact"
    ),
    "/api/control-room/items/{item_id}/activity": (
        f"/api/control-room/items/{ENCODED_ITEM_ID}/activity"
    ),
    "/api/control-room/items/{item_id}/action-runs": (
        f"/api/control-room/items/{ENCODED_ITEM_ID}/action-runs"
    ),
    "/api/control-room/items/{item_id}/outcomes": (
        f"/api/control-room/items/{ENCODED_ITEM_ID}/outcomes"
    ),
    "/api/control-room/anomalies/{anomaly_id}": (
        f"/api/control-room/anomalies/{ENCODED_ITEM_ID}"
    ),
    "/api/control-room/thresholds": "/api/control-room/thresholds",
    "/api/control-room/lessons": (
        "/api/control-room/lessons?cartridge_id=sap_hcm"
        f"&anomaly_type=risk&item_id={ENCODED_ITEM_ID}"
    ),
}

GET_CASES = tuple(
    {
        "route": route,
        "path": path,
        "status": 200,
        "invariant": lambda payload: payload is not None,
    }
    for route, path in GET_PATHS.items()
)


def _stable_response(value):
    if isinstance(value, dict):
        return {
            key: _stable_response(item)
            for key, item in value.items()
            if key
            not in {
                "checked_at",
                "due_at",
                "generated_at",
                "latency_ms",
                "updated_at",
            }
        }
    if isinstance(value, list):
        return [_stable_response(item) for item in value]
    return value


@pytest.mark.asyncio
async def test_all_29_get_routes_are_asgi_pure_repeatable_and_concurrent():
    discovered = {
        route.path
        for route in routes.router.routes
        if "GET" in (route.methods or set())
    }
    assert len(GET_PATHS) == 29
    assert discovered == set(GET_PATHS)

    sentinel = MutationSentinel()
    before = sentinel.snapshot()
    probe = ConcurrencyProbe()
    app = build_app()
    routes._CONTROL_ROOM_READ_CACHE.clear()
    routes._CONTROL_ROOM_READ_CACHE_LOCKS.clear()
    transport = httpx.ASGITransport(app=app)

    seen_paths: list[str] = []
    with installed_read_edges(sentinel, probe):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            for case in GET_CASES:
                path = case["path"]
                first = await client.get(path)
                second = await client.get(path)
                seen_paths.append(path)
                assert first.status_code == case["status"], (path, first.text)
                assert second.status_code == first.status_code
                first_payload = first.json()
                assert case["invariant"](first_payload), path
                assert _stable_response(first_payload) == _stable_response(
                    second.json()
                )

            probe.enabled = True
            first, second = await asyncio.gather(
                client.get(
                    "/api/control-room/dashboard",
                    headers={"x-purity-request": "request-a"},
                ),
                client.get(
                    "/api/control-room/dashboard",
                    headers={"x-purity-request": "request-b"},
                ),
            )

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(seen_paths) == 29
    assert probe.max_in_flight >= 2
    assert sentinel.snapshot() == before
    assert sentinel.mutation_attempts == []
    request_ids = {entry[0] for entry in sentinel.scope_calls}
    assert {"request-a", "request-b"}.issubset(request_ids)
    assert all(entry[1:] == (TENANT_ID, WORKSPACE_ID) for entry in sentinel.scope_calls)
