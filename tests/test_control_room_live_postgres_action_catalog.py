from __future__ import annotations

from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from app.services import auth
from app.services.control_room.business_action_catalog import (
    load_enabled_action_template_ids,
)
from tests.test_operational_rls_console_refinement import (
    postgres_with_real_init_schema,
)


USER = {
    "id": 1,
    "role": "super_admin",
    "active_tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "active_workspace_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
}


@pytest.mark.asyncio
async def test_real_postgres_action_catalog_matches_enabled_known_templates(
    postgres_with_real_init_schema: str,
):
    pool = await asyncpg.create_pool(
        postgres_with_real_init_schema,
        min_size=1,
        max_size=2,
    )
    try:
        async with pool.acquire() as conn:
            expected = frozenset(
                await conn.fetchval(
                    "SELECT array_agg(template_id ORDER BY template_id) "
                    "FROM control_room_action_templates WHERE enabled IS TRUE"
                )
                or []
            )
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            actual = await load_enabled_action_template_ids(USER)
    finally:
        await pool.close()

    assert actual
    assert actual == expected
    assert "request_owner_review" in actual
