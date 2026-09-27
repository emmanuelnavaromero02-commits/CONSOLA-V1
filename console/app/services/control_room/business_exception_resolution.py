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
from app.services.control_room.business_cartridge_scope import (
    allowed_business_cartridges,
)
from app.services.db_scope import run_with_db_scope


_LOGGER = logging.getLogger(__name__)
_READ_FAILURE = "control_room_exception_resolution_read_failure"
MAX_APPROVED_EXCEPTIONS = 20
APPROVED_EXCEPTIONS_SQL = """
SELECT item.tenant_id::text AS tenant_id,
       item.workspace_id::text AS workspace_id,
       item.owner_user_id, item.item_id, item.cartridge_id, item.domain,
       item.source_dataset, item.item_kind, item.title, item.severity,
       item.status, item.decision_id, item.entity_kind, item.entity_id,
       item.entity_label, item.anomaly_type, item.metadata,
       item.first_seen_at, item.last_seen_at, item.resolved_at,
       item.dismissed_at, item.selected_option_id, item.execution_status
  FROM control_room_items AS item
 WHERE item.workspace_id = $1::uuid
   AND item.tenant_id = $2::uuid
   AND item.status = 'dismissed'
   AND item.metadata->>'resolution' = 'exception_approved'
   AND ($3::bigint IS NULL OR item.owner_user_id = $3::bigint)
   AND ($4::text[] IS NULL OR item.cartridge_id = ANY($4::text[]))
 ORDER BY item.metadata->>'resolution_at' DESC, item.item_id
 LIMIT $5
"""


@dataclass(frozen=True, repr=False)
class ExceptionResolution:
    approved_at: datetime | None
    reason: str | None
    actor_user_id: int | None


@dataclass(frozen=True, repr=False)
class ApprovedException:
    item_id: str
    row: Mapping[str, Any]
    resolution: ExceptionResolution


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


async def load_approved_exceptions(
    user: Mapping[str, Any],
) -> list[ApprovedException]:
    owner_id = None if can_read_workspace_wide(user) else owner_scope_id(user) or 0
    allowed = allowed_business_cartridges(user)
    cartridges = None if allowed is None else sorted({*allowed, "platform"})

    async def _read(conn: Any, tenant_id: str | None, workspace_id: str) -> Any:
        return await conn.fetch(
            APPROVED_EXCEPTIONS_SQL,
            workspace_id,
            str(tenant_id or ""),
            owner_id,
            cartridges,
            MAX_APPROVED_EXCEPTIONS,
        )

    try:
        pool = await auth.pool()
        rows = await run_with_db_scope(pool, dict(user), _read)
    except Exception:
        _LOGGER.error(
            _READ_FAILURE,
            extra={"event": _READ_FAILURE, "outcome": "exceptions_omitted"},
        )
        return []
    approved: list[ApprovedException] = []
    for raw in rows or ():
        row = dict(raw)
        if str(row.get("status") or "").strip().lower() != "dismissed":
            continue
        resolution = exception_resolution(_metadata(row.get("metadata")))
        item_id = str(row.get("item_id") or "").strip()
        if resolution is None or not item_id:
            continue
        approved.append(ApprovedException(item_id, row, resolution))
    return approved[:MAX_APPROVED_EXCEPTIONS]


__all__ = (
    "APPROVED_EXCEPTIONS_SQL",
    "ApprovedException",
    "ExceptionResolution",
    "MAX_APPROVED_EXCEPTIONS",
    "exception_resolution",
    "load_approved_exceptions",
)
