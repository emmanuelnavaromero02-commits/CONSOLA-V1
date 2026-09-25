from __future__ import annotations

from collections.abc import Iterable
from typing import Any


JOB_RUNNER_FAILED = "job_runner_failed"
CATALOG_SEED_FAILED = "catalog_seed_failed"
STARTUP_COMPONENT_FAILED = "startup_component_failed"
MCP_PROBE_FAILED = "mcp_probe_failed"

_SAFE_STARTUP_CODES = frozenset(
    {
        JOB_RUNNER_FAILED,
        CATALOG_SEED_FAILED,
        STARTUP_COMPONENT_FAILED,
    }
)


def safe_startup_errors(value: Any) -> list[str]:

    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, dict)):
        return []
    safe: list[str] = []
    for item in value:
        code = item if isinstance(item, str) and item in _SAFE_STARTUP_CODES else ""
        if not code and isinstance(item, str):
            if item.startswith("job_runner"):
                code = JOB_RUNNER_FAILED
            elif item.startswith("catalog_seed"):
                code = CATALOG_SEED_FAILED
        code = code or STARTUP_COMPONENT_FAILED
        if code not in safe:
            safe.append(code)
    return safe
