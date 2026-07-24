from __future__ import annotations

import json

import asyncpg
import pytest

from app.services.control_room.business_decision_persistence import (
    persist_option_selection,
)
from app.services.control_room.business_item_persistence import (
    OwnerScopeConflict,
    ensure_item_row,
    persist_item_rows,
)
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    workflow_has_eligible_provenance,
)
from tests.test_control_room_live_postgres_workflows import _item, _rows, _scope
from tests.test_operational_rls_console_refinement import (
    postgres_with_real_init_schema,
)


class AsyncNoop:
    async def __call__(self, *_args, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_live_option_selection_without_decision_survives_refresh(
    postgres_with_real_init_schema: str,
):
    conn = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        tenant_id, workspace_id = await _scope(conn)
        item = _item("option-only", tenant_id, workspace_id)
        row = _rows([item], tenant_id, workspace_id)[0]
        await persist_item_rows(conn, [row], owner_scope_id=7)
        await persist_option_selection(
            conn,
            user={
                "id": 7,
                "active_tenant_id": tenant_id,
                "active_workspace_id": workspace_id,
            },
            item={**item, "owner_user_id": 7},
            workspace_id=workspace_id,
            option_id="review",
            terminal_statuses=("approved", "resolved"),
            ensure_item_row=AsyncNoop(),
            record_item_event=AsyncNoop(),
        )
        await persist_item_rows(conn, [row], owner_scope_id=7)

        stored = dict(
            await conn.fetchrow(
                """SELECT * FROM control_room_items
                   WHERE workspace_id=$1 AND item_id=$2""",
                workspace_id,
                item["id"],
            )
        )
        metadata = json.loads(stored["metadata"])
        assert stored["decision_id"] is None
        assert stored["selected_option_id"] == "review"
        assert metadata[DECISION_PROVENANCE_KEY]["stage"] == "option_selected"
        assert workflow_has_eligible_provenance(
            metadata,
            {
                **item,
                "workspace_id": workspace_id,
                "selected_option_id": "review",
            },
            decision_id=None,
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_live_legacy_null_owner_is_admin_managed_and_never_claimed(
    postgres_with_real_init_schema: str,
):
    conn = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        tenant_id, workspace_id = await _scope(conn)
        item = _item("legacy-null-owner", tenant_id, workspace_id)
        row = _rows([item], tenant_id, workspace_id, owner=None)[0]
        await persist_item_rows(conn, [row], workspace_wide=True)

        claimed = {**row, "owner_user_id": 7, "status": "in_review"}
        with pytest.raises(OwnerScopeConflict):
            await ensure_item_row(
                conn,
                claimed,
                terminal_statuses=("approved", "resolved"),
                owner_scope_id=7,
            )

        await ensure_item_row(
            conn,
            {**row, "status": "in_review"},
            terminal_statuses=("approved", "resolved"),
            workspace_wide=True,
        )
        owner = await conn.fetchval(
            """SELECT owner_user_id FROM control_room_items
               WHERE workspace_id=$1 AND item_id=$2""",
            workspace_id,
            item["id"],
        )
        assert owner is None
    finally:
        await conn.close()
