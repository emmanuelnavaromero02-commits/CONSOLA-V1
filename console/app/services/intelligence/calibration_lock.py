from __future__ import annotations

from typing import Any


async def lock_calibration_group(
    conn: Any,
    *,
    workspace_id: str,
    group: str,
    model_version: str,
) -> None:
    """Serialize observation and full recompute for one calibration state."""
    key = f"calibration:{workspace_id}:{group}:{model_version}"
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", key)


__all__ = ("lock_calibration_group",)
