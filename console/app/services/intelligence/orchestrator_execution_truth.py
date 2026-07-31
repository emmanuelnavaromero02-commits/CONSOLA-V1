from __future__ import annotations

from typing import Any

from app.services.intelligence.source_provenance import source_is_trusted


def reject_client_bayesian_version(
    engine_inputs: dict[str, Any], error_type: type[Exception]
) -> None:
    raw = engine_inputs.get("bayesian_calibration") or {}
    if isinstance(raw, dict) and "model_version" in raw:
        raise error_type(422, "model_version is server-owned")


def incomplete_calibration_result(
    metrics: dict[str, Any],
) -> tuple[str, dict[str, str], list[Any], str, None] | None:
    if int(metrics.get("sample_count") or metrics.get("processed_total") or 0) <= 0:
        reason = "no_trusted_observations"
        return "skipped", {"status": "skipped", "reason": reason}, [], reason, None
    if (
        metrics.get("complete") is True
        and metrics.get("provenance_complete") is True
        and metrics.get("binary_evaluation_complete") is True
    ):
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


__all__ = (
    "execution_source_trusted",
    "incomplete_calibration_result",
    "reject_client_bayesian_version",
)
