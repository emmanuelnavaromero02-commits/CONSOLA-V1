from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.schemas.control_room_live import (
    CONTROL_ROOM_FRESHNESS_SCHEMA_VERSION,
    ControlRoomFreshnessResponse,
)
from app.services import auth
from app.services.control_room.business_access import (
    can_read_workspace_wide,
    owner_scope_id,
)
from app.services.control_room.business_action_catalog import (
    ENABLED_ACTION_TEMPLATE_IDS_SQL,
)
from app.services.control_room.business_action_digest import action_contract_digest
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_cartridge_scope import (
    allowed_business_cartridges,
)
from app.services.control_room.cache_identity import authorization_cache_identity
from app.services.db_scope import run_with_db_scope
from app.services.permissions import has_permission


FRESHNESS_VERSION = "control-room-freshness-inputs/v1"
READ_ONLY_TRANSACTION_SQL = "SET TRANSACTION READ ONLY"
MAX_GOLD_DATASETS = 500
_KNOWN_TEMPLATE_IDS = tuple(sorted(ACTION_TEMPLATES))

ITEM_STATUS_SQL = """
SELECT status, count(*) AS item_count, max(last_seen_at) AS last_seen_at
  FROM control_room_items
 WHERE workspace_id = $1::uuid
   AND tenant_id = $2::uuid
   AND ($3::bigint IS NULL OR owner_user_id = $3::bigint)
 GROUP BY status
 ORDER BY status
"""
ITEM_EVENTS_SQL = """
SELECT max(event.id) AS last_event_id, count(event.id) AS event_count
  FROM control_room_item_events AS event
  JOIN control_room_items AS item
    ON item.workspace_id = event.workspace_id
   AND item.item_id = event.item_id
 WHERE event.workspace_id = $1::uuid
   AND item.tenant_id = $2::uuid
   AND ($3::bigint IS NULL OR item.owner_user_id = $3::bigint)
"""
THRESHOLDS_SQL = """
SELECT max(updated_at) AS updated_at, count(*) AS threshold_count
  FROM control_room_thresholds
 WHERE workspace_id = $1::uuid
"""
INSTALLATIONS_SQL = """
SELECT max(updated_at) AS updated_at, count(*) AS installation_count
  FROM cartridge_installations
 WHERE workspace_id = $1::uuid
   AND tenant_id = $2::uuid
"""
GOLD_DATASETS_SQL = """
SELECT name, cartridge, last_refresh, row_count, updated_at
  FROM datasets
 WHERE workspace_id = $1::uuid
   AND layer = 'gold'
   AND ($2::text[] IS NULL OR cartridge = ANY($2::text[]))
 ORDER BY name
 LIMIT $3
"""


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _row(value: Any) -> dict[str, Any]:
    return {str(key): _plain(item) for key, item in dict(value or {}).items()}


def _rows(values: Sequence[Any] | None) -> list[dict[str, Any]]:
    return [_row(value) for value in (values or ())]


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    parsed = value if value.tzinfo else value.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _latest_refresh(rows: Sequence[Mapping[str, Any]]) -> datetime | None:
    moments = [
        moment
        for moment in (_utc(row.get("last_refresh")) for row in rows)
        if moment is not None
    ]
    return max(moments) if moments else None


def _identity_digest(user: Mapping[str, Any]) -> str:
    return action_contract_digest(asdict(authorization_cache_identity(dict(user))))


async def compute_experience_fingerprint(
    user: Mapping[str, Any],
) -> ControlRoomFreshnessResponse:
    owner_id = None if can_read_workspace_wide(user) else owner_scope_id(user) or 0
    allowed = allowed_business_cartridges(user)
    cartridges = None if allowed is None else sorted(allowed)
    writer = has_permission(dict(user), "control_room.write")
    pool = await auth.pool()

    async def _read(
        conn: Any, tenant_id: str | None, workspace_id: str
    ) -> tuple[dict[str, Any], datetime | None]:
        await conn.execute(READ_ONLY_TRANSACTION_SQL)
        tenant = str(tenant_id or "")
        items = await conn.fetch(ITEM_STATUS_SQL, workspace_id, tenant, owner_id)
        events = await conn.fetchrow(ITEM_EVENTS_SQL, workspace_id, tenant, owner_id)
        thresholds = await conn.fetchrow(THRESHOLDS_SQL, workspace_id)
        installations = await conn.fetchrow(INSTALLATIONS_SQL, workspace_id, tenant)
        gold: list[Any] = []
        if cartridges is None or cartridges:
            gold = list(
                await conn.fetch(
                    GOLD_DATASETS_SQL, workspace_id, cartridges, MAX_GOLD_DATASETS
                )
            )
        templates: list[Any] = []
        if writer:
            templates = list(
                await conn.fetch(
                    ENABLED_ACTION_TEMPLATE_IDS_SQL,
                    list(_KNOWN_TEMPLATE_IDS),
                    len(_KNOWN_TEMPLATE_IDS),
                )
            )
        gold_rows = _rows(gold)
        return (
            {
                "version": FRESHNESS_VERSION,
                "tenant_id": tenant,
                "workspace_id": workspace_id,
                "items": _rows(items),
                "events": _row(events),
                "thresholds": _row(thresholds),
                "installations": _row(installations),
                "gold_datasets": gold_rows,
                "templates": _rows(templates),
            },
            _latest_refresh([dict(value) for value in gold]),
        )

    inputs, data_refreshed_at = await run_with_db_scope(pool, dict(user), _read)
    inputs["authorization"] = _identity_digest(user)
    return ControlRoomFreshnessResponse(
        schema_version=CONTROL_ROOM_FRESHNESS_SCHEMA_VERSION,
        fingerprint=action_contract_digest(inputs),
        checked_at=datetime.now(UTC),
        data_refreshed_at=data_refreshed_at,
    )


__all__ = (
    "GOLD_DATASETS_SQL",
    "INSTALLATIONS_SQL",
    "ITEM_EVENTS_SQL",
    "ITEM_STATUS_SQL",
    "READ_ONLY_TRANSACTION_SQL",
    "THRESHOLDS_SQL",
    "compute_experience_fingerprint",
)
