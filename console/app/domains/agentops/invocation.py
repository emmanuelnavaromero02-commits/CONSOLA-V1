from __future__ import annotations

from typing import Any

from fastapi import HTTPException


async def invoke_agent_payload(
    *,
    agent_id: str,
    body: dict,
    user: dict,
    agents_service: Any,
    agent_runtime: Any,
    background_requested: Any,
    start_background: Any,
    background_response: Any,
) -> dict:
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
    if background_requested(body):
        start_background(agent, message, history, user)
        return background_response(agent)
    return await agent_runtime.run(agent, message, history=history, user=user)


def agent_invoke_background_requested(body: dict) -> bool:
    if not isinstance(body, dict):
        return False
    if body.get("background") is True or body.get("queued") is True:
        return True
    if body.get("async") is True:
        return True
    return body.get("wait") is False


def agent_invoke_background_response(agent: Any) -> dict[str, Any]:
    return {
        "reply": "Ejecucion iniciada. Revisa la pestana Ejecuciones para ver el resultado.",
        "viewer_urls": [],
        "messages": [],
        "agent_id": getattr(agent, "id", None),
        "run_id": None,
        "status": "queued",
        "queued": True,
    }


def parse_agent_scheduled_fire_at(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        from datetime import datetime as _dt, timezone as _tz

        parsed = _dt.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_tz.utc)
        return parsed.astimezone(_tz.utc)
    except Exception as exc:
        raise HTTPException(400, "scheduled_fire_at is invalid") from exc


def agent_schedule_due(
    schedule: dict,
    scheduled_fire_at: Any | None = None,
    *,
    interval_minutes: int,
    grace_minutes: int,
) -> bool:
    cron_expr = str(
        schedule.get("cron") or schedule.get("cron_expression") or ""
    ).strip()
    if not cron_expr:
        return False
    try:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        from zoneinfo import ZoneInfo

        from croniter import croniter
    except Exception as exc:
        raise HTTPException(503, "cron scheduler dependency unavailable") from exc
    try:
        tz = ZoneInfo(str(schedule.get("tz") or "UTC"))
        if scheduled_fire_at is not None:
            fire_utc = scheduled_fire_at.astimezone(_tz.utc)
            iterator = croniter(cron_expr, fire_utc.astimezone(tz) - _td(seconds=1))
            next_fire = iterator.get_next(_dt)
            if next_fire.tzinfo is None:
                next_fire = next_fire.replace(tzinfo=tz)
            next_fire_utc = next_fire.astimezone(_tz.utc)
            return abs((next_fire_utc - fire_utc).total_seconds()) <= 1

        now_utc = _dt.now(_tz.utc)
        window_start_utc = now_utc - _td(
            minutes=max(interval_minutes, 1) + grace_minutes
        )
        window_end_utc = now_utc + _td(minutes=grace_minutes)
        iterator = croniter(cron_expr, window_start_utc.astimezone(tz))
        next_fire = iterator.get_next(_dt)
        if next_fire.tzinfo is None:
            next_fire = next_fire.replace(tzinfo=tz)
        next_fire_utc = next_fire.astimezone(_tz.utc)
    except Exception as exc:
        raise HTTPException(403, "agent schedule cron is invalid") from exc
    return window_start_utc <= next_fire_utc <= window_end_utc
