from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ExactPopulation:
    value: int | None
    status: str
    error: str | None


@dataclass(frozen=True)
class CoverageDecision:
    value: int | None
    status: str
    error: str | None
    publish_observed_subset: bool = False


def _strict_count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def exact_population(result: Mapping[str, object]) -> ExactPopulation:
    status = str(result.get("status") or "unavailable")
    value = _strict_count(result.get("total"))
    error_value = result.get("error")
    error = str(error_value) if error_value else None
    if status == "ready" and value is None:
        return ExactPopulation(None, "invalid_schema", "invalid active headcount")
    return ExactPopulation(value if status == "ready" else None, status, error)


def dimension_coverage(
    result: Mapping[str, object],
    exact: ExactPopulation,
) -> CoverageDecision:
    """Classify whether a filtered dimension covers the exact population."""
    source_status = str(result.get("status") or "unavailable")
    source_error_value = result.get("error")
    source_error = str(source_error_value) if source_error_value else None
    dimension_total = _strict_count(result.get("total"))

    if source_status == "empty":
        if exact.status == "ready" and exact.value == 0:
            return CoverageDecision(None, "empty", source_error)
        if exact.status == "ready" and exact.value is not None:
            return CoverageDecision(
                None,
                "partial",
                f"dimension covers 0 of {exact.value} active employees",
            )
        return CoverageDecision(None, "partial", exact.error or source_error)
    if source_status != "ready":
        return CoverageDecision(None, source_status, source_error)
    if dimension_total is None:
        return CoverageDecision(None, "invalid_schema", "invalid dimensional total")
    if exact.status != "ready" or exact.value is None:
        return CoverageDecision(
            dimension_total,
            "partial",
            exact.error or "exact active headcount unavailable",
        )
    if dimension_total > exact.value:
        return CoverageDecision(
            None,
            "invalid_schema",
            "dimensional headcount exceeds exact active headcount",
        )
    if dimension_total < exact.value:
        return CoverageDecision(
            dimension_total,
            "partial",
            f"dimension covers {dimension_total} of {exact.value} active employees",
            publish_observed_subset=True,
        )
    return CoverageDecision(dimension_total, "ready", source_error)


__all__ = (
    "CoverageDecision",
    "ExactPopulation",
    "dimension_coverage",
    "exact_population",
)
