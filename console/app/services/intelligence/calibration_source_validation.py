from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.services.intelligence.calibration_authoritative_evidence import (
    resolve_authoritative_observation,
)
from app.services.intelligence.source_provenance import resolve_source_provenance


async def calibration_source_exists(
    conn: Any,
    *,
    workspace_id: str,
    source_type: str,
    source_id: str,
    allow_manual: bool = False,
) -> bool:
    if source_type in {"decision_option", "monte_carlo_simulation"}:
        return False
    if source_type == "prediction_outcome":
        try:
            await resolve_authoritative_observation(
                conn,
                workspace_id=workspace_id,
                payload={
                    "source_type": source_type,
                    "source_id": source_id,
                },
                allow_manual=False,
            )
        except HTTPException:
            return False
        return True
    result = await resolve_source_provenance(
        conn,
        workspace_id=workspace_id,
        source_type=source_type,
        source_id=source_id,
        allow_manual=allow_manual,
    )
    return result.trusted


__all__ = ("calibration_source_exists",)
