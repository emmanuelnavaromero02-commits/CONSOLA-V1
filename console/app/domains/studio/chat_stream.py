from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from starlette.responses import StreamingResponse


async def studio_chat_stream_response(
    *,
    body: dict[str, Any],
    user: dict[str, Any],
    cartridge_service: Any,
    studio_assistant: Any,
    uuid_factory: Callable[[], Any],
    logger_exception: Callable[..., Any],
    streaming_response_factory: Callable[..., StreamingResponse] = StreamingResponse,
) -> StreamingResponse:

    cartridge_id = body.get("cartridge_id")
    manifest = (
        await cartridge_service.get_cartridge(cartridge_id) if cartridge_id else None
    )
    message = (body.get("message") or "").strip()
    history = body.get("history") or []
    step = body.get("step", 1)
    if not message:
        raise HTTPException(400, "message is required")

    queue: asyncio.Queue = asyncio.Queue()

    async def on_event(evt: dict):
        await queue.put(evt)

    async def run():
        try:
            result = await studio_assistant.chat(
                message=message,
                history=history,
                step=step,
                manifest=manifest,
                on_event=on_event,
                actor_role=user.get("role"),
                actor_user=user,
            )
            await queue.put({"type": "done", **result})
        except Exception:
            error_id = uuid_factory().hex
            logger_exception("studio assistant chat failed error_id=%s", error_id)
            await queue.put(
                {
                    "type": "error",
                    "message": f"Internal server error. error_id={error_id}",
                }
            )

    asyncio.create_task(run())

    async def event_stream():
        yield "event: open\ndata: {}\n\n"
        while True:
            evt = await queue.get()
            etype = evt.get("type", "message")
            yield f"event: {etype}\ndata: {json.dumps(evt, default=str)}\n\n"
            if etype in ("done", "error"):
                break

    return streaming_response_factory(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
