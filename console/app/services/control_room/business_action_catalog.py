from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from app.services import auth
from app.services.control_room.business_action_binding import (
    valid_action_template_binding,
)
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.db_scope import run_with_db_scope


_KNOWN_TEMPLATE_IDS = tuple(sorted(ACTION_TEMPLATES))
ENABLED_ACTION_TEMPLATE_IDS_SQL = """
SELECT template_id, cartridge_id, label, requires_approval
FROM control_room_action_templates
WHERE enabled IS TRUE
  AND template_id = ANY($1::text[])
ORDER BY template_id
LIMIT $2
"""
ENABLED_ACTION_TEMPLATE_SQL = """
SELECT template_id, cartridge_id, label, requires_approval
FROM control_room_action_templates
WHERE template_id = $1
  AND enabled IS TRUE
LIMIT 1
FOR SHARE
"""


def _matches_runtime_registry(row: Mapping[str, Any]) -> bool:
    template_id = str(row.get("template_id") or "")
    template = ACTION_TEMPLATES.get(template_id)
    return bool(
        template
        and str(row.get("cartridge_id") or "")
        == str(template.get("cartridge_id") or "")
        and valid_action_template_binding(
            template_id,
            label=str(row.get("label") or ""),
            requires_approval=row.get("requires_approval") is True,
        )
    )


async def load_enabled_action_template_ids(
    user: Mapping[str, Any],
) -> frozenset[str]:
    pool = await auth.pool()

    async def _read(
        conn: Any,
        _tenant_id: str | None,
        _workspace_id: str,
    ) -> frozenset[str]:
        rows = await conn.fetch(
            ENABLED_ACTION_TEMPLATE_IDS_SQL,
            list(_KNOWN_TEMPLATE_IDS),
            len(_KNOWN_TEMPLATE_IDS),
        )
        return frozenset(
            str(row["template_id"]) for row in rows if _matches_runtime_registry(row)
        )

    return await run_with_db_scope(pool, dict(user), _read)


async def require_enabled_action_template(conn: Any, template_id: str) -> None:
    row = await conn.fetchrow(ENABLED_ACTION_TEMPLATE_SQL, template_id)
    if not row or not _matches_runtime_registry(row):
        raise HTTPException(404, "action template not found")


__all__ = (
    "ENABLED_ACTION_TEMPLATE_IDS_SQL",
    "ENABLED_ACTION_TEMPLATE_SQL",
    "load_enabled_action_template_ids",
    "require_enabled_action_template",
)
