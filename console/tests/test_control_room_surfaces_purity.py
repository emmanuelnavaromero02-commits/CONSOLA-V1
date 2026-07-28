from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import Mock, patch

import httpx
import pytest

from app.routers import control_room as routes
from app.services import control_room_service
from control_room_get_assertions import stable_response
from control_room_get_edges import installed_read_edges
from control_room_get_harness import ConcurrencyProbe, MutationSentinel, build_app


SURFACE_PATHS = (
    "/api/control-room/experience",
    "/api/control-room/experience/v2",
    "/api/control-room/diagnostics",
)


@pytest.mark.asyncio
async def test_surfaces_are_repeatable_concurrent_and_free_of_dml():
    sentinel = MutationSentinel()
    before = deepcopy(sentinel.tables)
    probe = ConcurrencyProbe()
    app = build_app(probe)
    transport = httpx.ASGITransport(app=app)

    with installed_read_edges(sentinel, probe):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            for index, path in enumerate(SURFACE_PATHS):
                first = await client.get(
                    path,
                    headers={"x-purity-request": f"surface-{index}-first"},
                )
                second = await client.get(
                    path,
                    headers={"x-purity-request": f"surface-{index}-second"},
                )
                assert first.status_code == second.status_code == 200
                assert stable_response(first.json()) == stable_response(second.json())

                probe.reset()
                probe.enabled = True
                concurrent = await asyncio.gather(
                    client.get(
                        path,
                        headers={"x-purity-request": f"surface-{index}-parallel-a"},
                    ),
                    client.get(
                        path,
                        headers={"x-purity-request": f"surface-{index}-parallel-b"},
                    ),
                )
                probe.enabled = False
                assert [response.status_code for response in concurrent] == [200, 200]
                assert stable_response(concurrent[0].json()) == stable_response(
                    concurrent[1].json()
                )
                assert probe.max_in_flight == 2

    assert sentinel.tables == before
    assert sentinel.mutation_attempts == []


@pytest.mark.asyncio
async def test_surface_collection_uses_pure_projector_not_business_builders():
    sentinel = MutationSentinel()
    probe = ConcurrencyProbe()
    omega_builder = Mock(side_effect=AssertionError("OMEGA builder invoked"))
    dashboard_builder = Mock(side_effect=AssertionError("dashboard builder invoked"))
    app = build_app(probe)
    transport = httpx.ASGITransport(app=app)

    with (
        installed_read_edges(sentinel, probe),
        patch.object(control_room_service, "_with_omega", omega_builder),
        patch.object(control_room_service, "dashboard", dashboard_builder),
    ):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            responses = [
                await client.get(path, headers={"x-purity-request": path})
                for path in SURFACE_PATHS
            ]

    assert [response.status_code for response in responses] == [200] * len(
        SURFACE_PATHS
    )
    omega_builder.assert_not_called()
    dashboard_builder.assert_not_called()


def test_legacy_get_matrix_remains_exactly_29_routes():
    from test_control_room_all_get_purity import GET_PATHS

    legacy_paths = {
        route.path
        for route in routes.router.routes
        if "GET" in (route.methods or set()) and route.path in GET_PATHS
    }
    assert len(GET_PATHS) == 29
    assert legacy_paths == set(GET_PATHS)
