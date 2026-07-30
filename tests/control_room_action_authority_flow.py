from __future__ import annotations

from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import auth
from app.services.control_room import (
    business_action_binding_producer,
    business_action_intents,
)
from app.services.control_room.business_action_authoritative_item import (
    match_authoritative_item,
)
from app.services.control_room.business_action_authority_repository import (
    fetch_authoritative_rows,
)
from app.services.control_room.business_action_binding_producer import (
    issue_action_bindings,
)
from app.services.control_room.business_action_intents import promote_action_handle
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_action_authorization_snapshot import (
    capture_authorization_snapshot,
)
from app.services.db_scope import run_with_db_scope
from tests.control_room_action_authority_live import (
    AuthorityScope,
    AuthoritySeed,
    snapshot,
)
from tests.control_room_action_authority_dry_run import insert_authority_dry_run


async def issue_live_binding(
    seed: AuthoritySeed,
    pool: asyncpg.Pool,
    scope: AuthorityScope | None = None,
):
    current = scope or seed.first

    async def inspect(conn, _tenant_id, _workspace_id):
        rows = await fetch_authoritative_rows(
            conn,
            tenant_id=current.tenant_id,
            workspace_id=current.workspace_id,
            item_ids=(current.item_id,),
        )
        row = rows.get(current.item_id)
        authorization = await capture_authorization_snapshot(
            conn,
            tenant_id=current.tenant_id,
            workspace_id=current.workspace_id,
            actor_user_id=int(current.maker["id"]),
            permission="control_room.write",
        )
        assert authorization is not None
        return match_authoritative_item(
            current.item,
            row or {},
            authorization,
            ACTION_TEMPLATES["create_followup_task"],
        )

    assert await run_with_db_scope(pool, current.maker, inspect) is not None
    with patch.object(
        business_action_binding_producer.auth,
        "pool",
        new=AsyncMock(return_value=pool),
    ):
        issued = await issue_action_bindings(
            current.maker,
            snapshot(current),
            enabled_template_ids={
                "create_followup_task",
                "prepare_hcm_access_review",
            },
        )
    assert set(issued) == {current.item_id}
    (action,) = issued[current.item_id]
    assert action.label == "Crear seguimiento operativo"
    assert action.requires_approval is True
    assert action.enabled is True
    return action


async def promote_live_intent(
    seed: AuthoritySeed,
    pool: asyncpg.Pool,
    scope: AuthorityScope | None = None,
):
    current = scope or seed.first
    await insert_authority_dry_run(seed, current)
    action = await issue_live_binding(seed, pool, current)
    with (
        patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
        patch.object(
            business_action_intents,
            "collect_surface_snapshot",
            new=AsyncMock(return_value=snapshot(current)),
        ),
    ):
        promoted = await promote_action_handle(
            current.maker,
            action.action_handle,
        )
        with pytest.raises(HTTPException) as replay:
            await promote_action_handle(current.maker, action.action_handle)
    assert replay.value.status_code == 404
    return promoted


__all__ = ("issue_live_binding", "promote_live_intent")
