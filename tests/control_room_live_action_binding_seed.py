from __future__ import annotations

import json
from typing import Any

import asyncpg

from app.services import control_room_service as service
from app.services.control_room.business_command_item import (
    load_persisted_command_item,
)
from app.services.control_room.business_explicit_action_binding import (
    attach_explicit_action_binding,
)


async def persist_live_action_binding(
    admin_dsn: str,
    console_dsn: str,
    *,
    user: dict[str, Any],
    item_id: str,
    template_id: str,
) -> tuple[dict[str, Any], str]:
    pool = await asyncpg.create_pool(console_dsn, min_size=1, max_size=2)

    async def pool_factory():
        return pool

    try:
        item = await load_persisted_command_item(
            item_id,
            user,
            pool_factory=pool_factory,
            run_scoped=service._run_with_db_scope,  # noqa: SLF001
            item_statuses=service.ITEM_STATUSES,
            severity_weights=service.SEVERITY_WEIGHT,
        )
    finally:
        await pool.close()
    if item is None:
        raise AssertionError("live binding item was not readable")
    bound = attach_explicit_action_binding(item, template_id=template_id)
    bindings = bound["metadata"]["explicit_action_bindings"]
    binding = next(value for value in bindings if value["template_id"] == template_id)
    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(
            "UPDATE control_room_items SET metadata=jsonb_set("
            "metadata, '{explicit_action_bindings}', $3::jsonb, true) "
            "WHERE workspace_id=$1::uuid AND item_id=$2",
            str(user["active_workspace_id"]),
            item_id,
            json.dumps(bindings, sort_keys=True),
        )
    finally:
        await conn.close()
    return bound, str(binding["binding_id"])


__all__ = ("persist_live_action_binding",)
