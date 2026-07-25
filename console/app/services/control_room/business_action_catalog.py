from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services import auth
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.db_scope import run_with_db_scope


ENABLED_ACTION_TEMPLATE_IDS_SQL = """
SELECT template_id
FROM control_room_action_templates
WHERE enabled IS TRUE
ORDER BY template_id
"""


async def load_enabled_action_template_ids(
    user: Mapping[str, Any],
) -> frozenset[str]:
    pool = await auth.pool()

    async def _read(
        conn: Any,
        _tenant_id: str | None,
        _workspace_id: str,
    ) -> frozenset[str]:
        rows = await conn.fetch(ENABLED_ACTION_TEMPLATE_IDS_SQL)
        return frozenset(
            template_id
            for row in rows
            if (template_id := str(row.get("template_id") or "")) in ACTION_TEMPLATES
        )

    return await run_with_db_scope(pool, dict(user), _read)


__all__ = (
    "ENABLED_ACTION_TEMPLATE_IDS_SQL",
    "load_enabled_action_template_ids",
)
