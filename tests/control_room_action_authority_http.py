from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, Request

from app.routers import control_room
from app.services.control_room import business_action_binding_producer
from tests.control_room_action_authority_live import snapshot

FOLLOWUP_TEMPLATES = frozenset({"create_followup_task"})


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


async def _issue(scope) -> dict:
    current = snapshot(scope)
    issued = await business_action_binding_producer.issue_action_bindings(
        scope.maker, current, enabled_template_ids=FOLLOWUP_TEMPLATES
    )
    return {
        "sections": [
            {
                "facts": [
                    {
                        "actions": [
                            action.model_dump(mode="json", exclude_none=True)
                            for action in issued.get(
                                str(item.get("id") or item.get("item_id")), ()
                            )
                        ]
                    }
                    for item in current.items
                ]
            }
        ]
    }


async def experience_gets(pool, scope, *, count: int, concurrent: bool):
    with patch.object(
        business_action_binding_producer.auth,
        "pool",
        new=AsyncMock(return_value=pool),
    ):
        if concurrent:
            return list(await asyncio.gather(*(_issue(scope) for _ in range(count))))
        return [await _issue(scope) for _ in range(count)]


__all__ = ("experience_gets",)
