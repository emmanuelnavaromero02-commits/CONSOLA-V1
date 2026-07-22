from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

from app.services.control_room.business_fingerprint import (
    business_observation_fingerprint,
)
from app.services.control_room.business_observation_codec import semantic_surfaces


OBSERVATION_ORDER_KEY = "business_observation_order"
OBSERVATION_ORDER_BASELINE_KEY = "_business_observation_order_baseline"
OBSERVATION_ORDER_VERSION = 1
_TIME_FIELDS = (
    "observed_at",
    "observation_date",
    "as_of",
    "detected_at",
    "generated_at",
    "freshness_at",
    "period_key",
)


def _normalized_instant(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day, tzinfo=UTC)
    else:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            if len(raw) == 10:
                parsed_date = date.fromisoformat(raw)
                parsed = datetime(
                    parsed_date.year,
                    parsed_date.month,
                    parsed_date.day,
                    tzinfo=UTC,
                )
            else:
                parsed = datetime.fromisoformat(
                    raw.replace("Z", "+00:00").replace("z", "+00:00")
                )
        except ValueError:
            return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (
        parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _latest_observation_instant(item: Mapping[str, Any]) -> str:
    instants = {
        normalized
        for surface in semantic_surfaces(item)
        for field in _TIME_FIELDS
        if field in surface
        if (normalized := _normalized_instant(surface.get(field)))
    }
    return max(instants, default="")


def business_observation_order(item: Mapping[str, Any]) -> str:
    """Return a total order for convergent refresh persistence.

    The timestamp is authoritative when present. The observation fingerprint is a
    deterministic tie-breaker for corrected values carrying the same source time.
    """
    observed_at = _latest_observation_instant(item)
    fingerprint = business_observation_fingerprint(item)
    return f"{OBSERVATION_ORDER_VERSION:04d}|{observed_at}|{fingerprint}"


__all__ = (
    "OBSERVATION_ORDER_BASELINE_KEY",
    "OBSERVATION_ORDER_KEY",
    "OBSERVATION_ORDER_VERSION",
    "business_observation_order",
)
