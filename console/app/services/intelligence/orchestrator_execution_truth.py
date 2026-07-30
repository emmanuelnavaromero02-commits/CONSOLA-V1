from __future__ import annotations

from typing import Any

from app.services.intelligence.source_provenance import source_is_trusted


def incomplete_calibration_result(
    metrics: dict[str, Any],
) -> tuple[str, dict[str, str], list[Any], str, None] | None:
    if metrics.get("complete") is True and metrics.get("provenance_complete") is True:
        return None
    reason = "incomplete_calibration_provenance"
    return "skipped", {"status": "skipped", "reason": reason}, [], reason, None


async def execution_source_trusted(
    conn: Any,
    workspace_id: str,
    run: dict[str, Any],
    allow_manual: bool,
) -> bool:
    return await source_is_trusted(
        conn,
        workspace_id,
        str(run.get("source_type") or ""),
        str(run.get("source_id") or ""),
        allow_manual,
    )


__all__ = ("execution_source_trusted", "incomplete_calibration_result")
