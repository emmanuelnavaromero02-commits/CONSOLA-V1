from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from starlette.responses import StreamingResponse


async def agent_invoke_stream_response(
    *,
    agent_id: str,
    body: dict[str, Any],
    user: dict[str, Any],
    agents_service: Any,
    agent_runtime: Any,
    streaming_response_factory: Callable[..., StreamingResponse] = StreamingResponse,
) -> StreamingResponse:

    visible = await agents_service.get_agent(agent_id, user_context=user)
    if not visible:
        raise HTTPException(404, "agent not found")
    agent = await agent_runtime.load_agent(agent_id, user_context=user)
    if not agent:
        raise HTTPException(404, "agent not found")
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message is required")
    history = body.get("history") or []

    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(ev: dict):
        await queue.put(ev)

    async def runner():
        try:
            result = await agent_runtime.run(
                agent, message, history=history, user=user, on_event=on_event
            )
            await queue.put({"type": "done", "run_id": result.get("run_id")})
        except Exception as exc:  # noqa: BLE001
            await queue.put(
                {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
            )
        finally:
            await queue.put(None)

    task = asyncio.create_task(runner())

    async def gen():
        try:
            while True:
                ev = await queue.get()
                if ev is None:
                    break
                yield f"data: {json.dumps(ev)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return streaming_response_factory(gen(), media_type="text/event-stream")
