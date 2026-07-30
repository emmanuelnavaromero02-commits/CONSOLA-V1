from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, Request

from app.routers import control_room, control_room_surfaces
from app.services.control_room import business_action_binding_producer
from tests.control_room_action_authority_live import snapshot


def _app(user: dict) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = user
        return await call_next(request)

    async def _current_user():
        return user

    app.dependency_overrides[control_room.require_authenticated] = _current_user
    app.include_router(control_room.router)
    return app


async def experience_gets(pool, scope, *, count: int, concurrent: bool):
    app = _app(scope.maker)
    transport = httpx.ASGITransport(app=app)
    with (
        patch.object(
            control_room_surfaces,
            "collect_surface_snapshot",
            new=AsyncMock(return_value=snapshot(scope)),
        ),
        patch.object(
            control_room_surfaces,
            "load_enabled_action_template_ids",
            new=AsyncMock(return_value=frozenset({"create_followup_task"})),
        ),
        patch.object(
            business_action_binding_producer.auth,
            "pool",
            new=AsyncMock(return_value=pool),
        ),
    ):
        async with httpx.AsyncClient(
            transport=transport, base_url="http://authority.test"
        ) as client:
            calls = [
                client.get("/api/control-room/experience/v2") for _ in range(count)
            ]
            responses = await asyncio.gather(*calls) if concurrent else []
            if not concurrent:
                for call in calls:
                    responses.append(await call)
    assert all(response.status_code == 200 for response in responses)
    return [response.json() for response in responses]


__all__ = ("experience_gets",)
