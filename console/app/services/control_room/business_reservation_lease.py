from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any


RESERVATION_LEASE_SECONDS = 300
RESERVATION_LEASE_TOKEN_KEY = "reservation_lease_token"


def reservation_lease_token(row: Mapping[str, Any]) -> str:
    metadata = row.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}
    if not isinstance(metadata, Mapping):
        return ""
    value = metadata.get(RESERVATION_LEASE_TOKEN_KEY)
    return str(value).strip() if isinstance(value, str) else ""


def reservation_lease_expired(row: Mapping[str, Any]) -> bool:
    updated_at = row.get("updated_at")
    if not isinstance(updated_at, datetime):
        return False
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return updated_at <= datetime.now(UTC) - timedelta(
        seconds=RESERVATION_LEASE_SECONDS
    )


__all__ = (
    "RESERVATION_LEASE_SECONDS",
    "RESERVATION_LEASE_TOKEN_KEY",
    "reservation_lease_expired",
    "reservation_lease_token",
)
