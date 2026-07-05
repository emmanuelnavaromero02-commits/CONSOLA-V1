from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.logging_config import _redact


logger = logging.getLogger("app.main")


def reset_startup_readiness_state(app: Any) -> None:
    app.state.startup_ok = True
    app.state.startup_errors = []


def record_startup_failure(
    app: Any,
    component: str,
    exc: Exception,
    *,
    critical: bool = True,
) -> None:
    raw_error = _redact(f"{type(exc).__name__}: {exc}") or type(exc).__name__
    entry = {
        "component": component,
        "critical": critical,
        "error": str(raw_error)[:300],
    }
    errors = list(getattr(app.state, "startup_errors", []) or [])
    errors.append(entry)
    app.state.startup_errors = errors
    if critical:
        app.state.startup_ok = False


async def run_startup_seed(
    app: Any,
    component: str,
    runner: Callable[[], Awaitable[Any]],
    *,
    critical: bool = True,
) -> None:
    try:
        await runner()
    except Exception as exc:
        record_startup_failure(app, component, exc, critical=critical)
        level = logger.error if critical else logger.warning
        label = "critical" if critical else "non-fatal"
        level("[startup] %s failed (%s): %s", component, label, exc, exc_info=True)


def startup_readiness_status(app: Any) -> dict[str, Any]:
    errors = list(getattr(app.state, "startup_errors", []) or [])
    critical_errors: list[dict[str, Any]] = []
    for error in errors:
        if isinstance(error, dict):
            if error.get("critical", True):
                critical_errors.append(error)
        else:
            critical_errors.append({"component": "startup", "critical": True})
    if not bool(getattr(app.state, "startup_ok", True)) or critical_errors:
        return {
            "status": "down",
            "critical_failures": len(critical_errors) or 1,
            "components": [
                str(error.get("component", "startup")) for error in critical_errors
            ],
        }
    return {"status": "up", "critical_failures": 0}
