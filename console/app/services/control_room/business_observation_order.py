from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_fingerprint import (
    business_observation_fingerprint,
    observation_instant,
)
from app.services.control_room.business_observation_codec import semantic_surfaces


OBSERVATION_ORDER_KEY = "business_observation_order"
OBSERVATION_ORDER_BASELINE_KEY = "_business_observation_order_baseline"
OBSERVATION_ORDER_VERSION = 2
_TIME_FIELDS = (
    "observed_at",
    "observation_date",
    "as_of",
    "period_key",
    "detected_at",
    "generated_at",
    "freshness_at",
)


def latest_observation_instant(item: Mapping[str, Any]) -> str:
    surfaces = semantic_surfaces(item)
    for field in _TIME_FIELDS:
        instants = {
            normalized
            for surface in surfaces
            if field in surface
            if (normalized := observation_instant(surface.get(field)))
        }
        if instants:
            return max(instants)
    return ""


def business_observation_order(item: Mapping[str, Any]) -> str:
    observed_at = latest_observation_instant(item)
    fingerprint = business_observation_fingerprint(item)
    return f"{OBSERVATION_ORDER_VERSION:04d}|{observed_at}|{fingerprint}"


def observation_order_instant(order: Any) -> str:
    parts = str(order or "").split("|", 2)
    return parts[1] if len(parts) == 3 else ""


__all__ = (
    "OBSERVATION_ORDER_BASELINE_KEY",
    "OBSERVATION_ORDER_KEY",
    "OBSERVATION_ORDER_VERSION",
    "business_observation_order",
    "latest_observation_instant",
    "observation_order_instant",
)
