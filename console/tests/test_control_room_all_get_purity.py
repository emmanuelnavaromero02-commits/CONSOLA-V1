from __future__ import annotations

import asyncio
from urllib.parse import quote

import httpx
import pytest

from app.routers import control_room as routes
from control_room_get_assertions import (
    assert_route_scope,
    matches_shape,
    stable_response,
)
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
    "/api/control-room/blackboard": "/api/control-room/blackboard?limit=6",
    "/api/control-room/lessons": (
        "/api/control-room/lessons?cartridge_id=sap_hcm"
        f"&anomaly_type=risk&item_id={ENCODED_ITEM_ID}"
    ),
    "/api/control-room/sap-b1/views/{view}": (
        "/api/control-room/sap-b1/views/sap_b1_sales_kpis?top_n=3"
    ),
}

READINESS_SHAPE = {
    "status": str,
    "series_count": int,
    "series": list,
    "usable_count": int,
}
ITEM_SHAPE = {"id": ITEM_ID, "kind": str, "omega": dict}
PAYLOAD_SHAPES = {
    "/api/control-room/summary": {"total_anomalies": int, "sources": list},
    "/api/control-room/dashboard": {
        "items": list,
        "summary": dict,
    },
    "/api/control-room/sap-successfactors/gold-kpis": {
        "widgets": list,
    },
    "/api/control-room/sap-successfactors/talent-kpis": {
        "readiness": dict,
        "widgets": list,
        "signals": list,
    },
    "/api/control-room/sap-successfactors/talent/overview": {
        "nine_box": dict,
        "anomalies": dict,
    },
    "/api/control-room/sap-successfactors/talent/9box": {
        "status": str,
        "cells": list,
        "totals": dict,
    },
    "/api/control-room/sap-successfactors/talent/9box/{box_id}": {
        "box": dict,
        "count": int,
        "roster": list,
    },
    "/api/control-room/sap-successfactors/talent/anomalies": {
        "status": str,
        "items": list,
        "summary": dict,
    },
    "/api/control-room/sap-successfactors/talent/metadata-readiness": {
        "status": str,
        "components": list,
        "source_check": dict,
    },
    "/api/control-room/banxico/readiness": READINESS_SHAPE,
    "/api/control-room/inegi/readiness": READINESS_SHAPE,
    "/api/control-room/sec-edgar/readiness": READINESS_SHAPE,
    "/api/control-room/sap-successfactors/market-validation": {
        "status": str,
        "policy": dict,
        "market_context": dict,
    },
    "/api/control-room/ops/summary": {
        "items": dict,
        "action_executions": dict,
    },
    "/api/control-room/blackboard": {
        "cycles": list,
        "signals": list,
        "lessons": list,
        "calibration": list,
    },
    "/api/control-room/agents/ops": {
        "agents": list,
        "summary": dict,
        "operational_diagnostics": list,
    },
    "/api/control-room/decision-intelligence/runs": {"runs": list},
    "/api/control-room/decision-intelligence/runs/{run_id}": {
        "run": dict,
        "snapshots": list,
    },
    "/api/control-room/decision-intelligence/history": {"history": list},
    "/api/control-room/decision-intelligence/calibration": {
        "status": str,
        "total_snapshots": int,
        "calibration_buckets": list,
    },
    "/api/control-room/alerts": {"alerts": list, "summary": dict},
    "/api/control-room/anomalies": {"anomalies": list, "sources": list},
    "/api/control-room/items/{item_id}": ITEM_SHAPE,
    "/api/control-room/items/{item_id}/impact": {
        "item_id": ITEM_ID,
        "estimate": float,
        "drivers": list,
    },
    "/api/control-room/items/{item_id}/activity": {
        "item_id": ITEM_ID,
        "activity": list,
        "counts": dict,
    },
    "/api/control-room/items/{item_id}/action-runs": {
        "item_id": ITEM_ID,
        "action_runs": list,
    },
    "/api/control-room/items/{item_id}/outcomes": {
        "item_id": ITEM_ID,
        "outcomes": list,
    },
    "/api/control-room/anomalies/{anomaly_id}": ITEM_SHAPE,
    "/api/control-room/thresholds": {"thresholds": list, "summary": dict},
    "/api/control-room/lessons": {"lessons": list, "summary": dict},
    "/api/control-room/sap-b1/views/{view}": {"domain": str, "status": str, "metrics": dict},
}


@pytest.mark.asyncio
async def test_all_31_get_routes_are_asgi_pure_repeatable_and_concurrent():
    discovered = {
        route.path
        for route in routes.router.routes
        if "GET" in (route.methods or set())
    }
    assert len(GET_PATHS) == 31
    assert set(GET_PATHS).issubset(discovered)
    assert discovered - set(GET_PATHS) == {
        "/api/control-room/experience",
        "/api/control-room/experience/v2",
        "/api/control-room/diagnostics",
    }

    sentinel = MutationSentinel()
    global_before = sentinel.snapshot()
    probe = ConcurrencyProbe()
    app = build_app(probe)
    routes._CONTROL_ROOM_READ_CACHE.clear()
    routes._CONTROL_ROOM_READ_CACHE_LOCKS.clear()
    transport = httpx.ASGITransport(app=app)

    seen_paths: list[str] = []
    with installed_read_edges(sentinel, probe):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            for index, (route, path) in enumerate(GET_PATHS.items()):
                route_before = sentinel.snapshot()
                scope_start = len(sentinel.scope_calls)
                query_start = len(sentinel.query_calls)
                request_ids = {
                    f"route-{index}-sequential-a",
                    f"route-{index}-sequential-b",
                    f"route-{index}-concurrent-a",
                    f"route-{index}-concurrent-b",
                }
                shape = PAYLOAD_SHAPES[route]
                first = await client.get(
                    path, headers={"x-purity-request": f"route-{index}-sequential-a"}
                )
                second = await client.get(
                    path, headers={"x-purity-request": f"route-{index}-sequential-b"}
                )
                seen_paths.append(path)
                assert first.status_code == 200, (path, first.text)
                assert second.status_code == first.status_code
                first_payload = first.json()
                assert matches_shape(first_payload, shape), path
                assert stable_response(first_payload) == stable_response(second.json())
                routes._CONTROL_ROOM_READ_CACHE.clear()
                routes._CONTROL_ROOM_READ_CACHE_LOCKS.clear()
                probe.reset()
                probe.enabled = True
                concurrent_a, concurrent_b = await asyncio.gather(
                    client.get(
                        path,
                        headers={"x-purity-request": f"route-{index}-concurrent-a"},
                    ),
                    client.get(
                        path,
                        headers={"x-purity-request": f"route-{index}-concurrent-b"},
                    ),
                )
                probe.enabled = False
                assert concurrent_a.status_code == 200, (
                    path,
                    concurrent_a.text,
                )
                assert concurrent_b.status_code == 200, (
                    path,
                    concurrent_b.text,
                )
                assert matches_shape(concurrent_a.json(), shape), path
                assert stable_response(concurrent_a.json()) == stable_response(
                    concurrent_b.json()
                )
                assert probe.max_in_flight >= 2, path
                assert sentinel.snapshot() == route_before, path
                assert_route_scope(
                    sentinel,
                    scope_start=scope_start,
                    query_start=query_start,
                    request_ids=request_ids,
                    expected_scope=(TENANT_ID, WORKSPACE_ID),
                )

    assert len(seen_paths) == 31
    assert sentinel.snapshot() == global_before
    assert sentinel.mutation_attempts == []
    scoped_request_ids = {entry[0] for entry in sentinel.scope_calls}
    assert all(entry[1:] == (TENANT_ID, WORKSPACE_ID) for entry in sentinel.scope_calls)
    assert sentinel.query_calls
    for request_id, statement, query_scope in sentinel.query_calls:
        assert request_id in scoped_request_ids, statement
        assert query_scope == (TENANT_ID, WORKSPACE_ID), (request_id, statement)
