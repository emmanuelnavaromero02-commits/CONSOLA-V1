from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.services import auth
from app.services.control_room.business_access import (
    actor_id,
    can_read_workspace_wide,
    owner_scope_id,
)
from app.services.control_room.business_action_direct_contract import (
    EXCEPTION_RESOLUTION,
)
from app.services.control_room.surface_snapshot import SurfaceSnapshot
from app.services.db_scope import run_with_db_scope


_LOGGER = logging.getLogger(__name__)
_READ_FAILURE = "control_room_exception_resolution_read_failure"
MAX_EXCEPTION_ROWS = 200
EXCEPTION_ROWS_SQL = """
SELECT item_id, metadata
  FROM control_room_items
 WHERE workspace_id = $1::uuid
   AND tenant_id = $2::uuid
   AND item_id = ANY($3::text[])
   AND status = 'dismissed'
   AND ($4::bigint IS NULL OR owner_user_id = $4::bigint)
 ORDER BY item_id
 LIMIT $5
"""


@dataclass(frozen=True, repr=False)
class ExceptionResolution:
    approved_at: datetime | None
    reason: str | None
    actor_user_id: int | None


def _metadata(value: Any) -> Mapping[str, Any]:
    if isinstance(value, (str, bytes, bytearray)):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, Mapping) else {}


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def exception_resolution(metadata: Mapping[str, Any]) -> ExceptionResolution | None:
    if metadata.get("resolution") != EXCEPTION_RESOLUTION:
        return None
    reason = metadata.get("resolution_reason")
    return ExceptionResolution(
        approved_at=_moment(metadata.get("resolution_at")),
        reason=reason.strip() if isinstance(reason, str) and reason.strip() else None,
        actor_user_id=actor_id(metadata.get("resolution_actor_id")),
    )


def _dismissed_item_ids(snapshot: SurfaceSnapshot) -> list[str]:
    return sorted(
        {
            str(item.get("id") or item.get("item_id") or "").strip()
            for item in snapshot.items
            if str(item.get("status") or "").strip().lower() == "dismissed"
        }
        - {""}
    )[:MAX_EXCEPTION_ROWS]


async def load_exception_resolutions(
    user: Mapping[str, Any], snapshot: SurfaceSnapshot
) -> dict[str, ExceptionResolution]:
    item_ids = _dismissed_item_ids(snapshot)
    if not item_ids:
        return {}
    owner_id = None if can_read_workspace_wide(user) else owner_scope_id(user) or 0
    tenant_id = snapshot.scope.tenant_id
    workspace_id = snapshot.scope.workspace_id

    async def _read(conn: Any, scoped_tenant: str | None, scoped_workspace: str):
        if scoped_tenant != tenant_id or scoped_workspace != workspace_id:
            return []
        return await conn.fetch(
            EXCEPTION_ROWS_SQL,
            workspace_id,
            tenant_id,
            item_ids,
            owner_id,
            MAX_EXCEPTION_ROWS,
        )

    try:
        pool = await auth.pool()
        rows = await run_with_db_scope(pool, dict(user), _read)
    except Exception:
        _LOGGER.error(
            _READ_FAILURE,
            extra={"event": _READ_FAILURE, "outcome": "exceptions_omitted"},
        )
        return {}
    resolutions: dict[str, ExceptionResolution] = {}
    for row in rows or ():
        resolution = exception_resolution(_metadata(row["metadata"]))
        if resolution is not None:
            resolutions[str(row["item_id"])] = resolution
    return resolutions


__all__ = (
    "EXCEPTION_ROWS_SQL",
    "ExceptionResolution",
    "exception_resolution",
    "load_exception_resolutions",
)
