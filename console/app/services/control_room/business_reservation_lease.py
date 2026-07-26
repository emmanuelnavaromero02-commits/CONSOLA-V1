from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any


RESERVATION_LEASE_SECONDS = 300


def reservation_lease_expired(row: Mapping[str, Any]) -> bool:
    updated_at = row.get("updated_at")
    if not isinstance(updated_at, datetime):
        return False
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return updated_at <= datetime.now(UTC) - timedelta(
        seconds=RESERVATION_LEASE_SECONDS
    )


__all__ = ("RESERVATION_LEASE_SECONDS", "reservation_lease_expired")
