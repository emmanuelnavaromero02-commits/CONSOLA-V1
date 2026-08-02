"""Typed HTTP contracts for the scheduled Agent Runner."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _object(response: Any) -> Mapping[str, Any]:
    if not 200 <= int(getattr(response, "status_code", 0)) < 300:
        raise RuntimeError("scheduled invocation unavailable")
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError("scheduled invocation unavailable") from exc
    if not isinstance(payload, Mapping) or not payload or "error" in payload:
        raise RuntimeError("scheduled invocation unavailable")
    return payload


def require_scheduled_invocation(response: Any) -> dict[str, Any]:
    payload = _object(response)
    if payload.get("ok") is not True or payload.get("status") != "completed":
        raise RuntimeError("scheduled invocation unavailable")
    duplicate = payload.get("duplicate", False)
    run_id = payload.get("run_id")
    schedule = payload.get("schedule_run")
    if (
        not isinstance(duplicate, bool)
        or isinstance(run_id, bool)
        or not isinstance(run_id, int)
        or run_id <= 0
        or not isinstance(schedule, Mapping)
        or schedule.get("status") != "ok"
        or isinstance(schedule.get("id"), bool)
        or not isinstance(schedule.get("id"), int)
        or isinstance(schedule.get("fencing_token"), bool)
        or not isinstance(schedule.get("fencing_token"), int)
    ):
        raise RuntimeError("scheduled invocation unavailable")
    if not duplicate:
        monitor = payload.get("monitor")
        if payload.get("deterministic_monitor") is not True or not isinstance(
            monitor, Mapping
        ):
            raise RuntimeError("scheduled invocation unavailable")
        for field in ("signals", "blockers"):
            value = monitor.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RuntimeError("scheduled invocation unavailable")
        if (
            not str(monitor.get("status") or "").strip()
            or not isinstance(monitor.get("engines"), list)
            or not isinstance(monitor.get("alerted"), bool)
        ):
            raise RuntimeError("scheduled invocation unavailable")
    return {
        "ok": True,
        "status": int(response.status_code),
        "duplicate": duplicate,
        "run_id": run_id,
    }


def require_registry_save(response: Any, *, expected_status: str) -> None:
    payload = _object(response)
    if "ok" in payload and payload.get("ok") is not True:
        raise RuntimeError("pipeline registry rejected agent runner result")
    result = payload.get("result")
    if (
        not isinstance(result, Mapping)
        or result.get("saved") is not True
        or result.get("status") != expected_status
    ):
        raise RuntimeError("pipeline registry rejected agent runner result")
