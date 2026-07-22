from __future__ import annotations

import json

import asyncpg
import pytest

from app.services.control_room.business_item_persistence import (
    ensure_item_row,
    persist_item_rows,
)
from app.services.control_room.business_observation_order import (
    business_observation_order,
)
from app.services.control_room.business_workflow_provenance import (
    DECISION_PROVENANCE_KEY,
    WORKFLOW_QUARANTINE_KEY,
    WorkflowStage,
    workflow_eligibility_provenance,
)
from tests.test_control_room_live_postgres_workflow_cycle import _item, _metadata
from tests.test_control_room_live_postgres_workflows import _rows, _scope
from tests.test_operational_rls_console_refinement import (
    postgres_with_real_init_schema,
)


@pytest.mark.asyncio
async def test_live_same_time_losing_fingerprint_cannot_quarantine_workflow(
    postgres_with_real_init_schema: str,
) -> None:
    conn = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        tenant_id, workspace_id = await _scope(conn)
        candidates = (
            _item(
                "same-time-order",
                tenant_id,
                workspace_id,
                value=10,
                observed_at="2026-07-22T12:00:00Z",
            ),
            _item(
                "same-time-order",
                tenant_id,
                workspace_id,
                value=20,
                observed_at="2026-07-22T12:00:00Z",
            ),
        )
        generations = [
            (item, _rows([item], tenant_id, workspace_id)[0]) for item in candidates
        ]
        (loser, loser_row), (winner, winner_row) = sorted(
            generations,
            key=lambda generation: business_observation_order(generation[1]),
        )
        loser_order = business_observation_order(loser_row)
        winner_order = business_observation_order(winner_row)
        assert loser_order.split("|", 2)[1] == winner_order.split("|", 2)[1]
        assert loser_order < winner_order

        await persist_item_rows(conn, [winner_row], owner_scope_id=7)
        provenance = workflow_eligibility_provenance(
            winner,
            stage=WorkflowStage.OPTION_SELECTED,
            workspace_id=workspace_id,
            option_id="review",
        )
        await conn.execute(
            """UPDATE control_room_items
                  SET status='in_review', selected_option_id='review',
                      execution_status='dry_run_validated',
                      metadata=metadata || $3::jsonb
                WHERE workspace_id=$1 AND item_id=$2""",
            workspace_id,
            winner["id"],
            json.dumps(
                {
                    "selected_option_id": "review",
                    DECISION_PROVENANCE_KEY: provenance,
                }
            ),
        )

        async def state() -> dict:
            stored = dict(
                await conn.fetchrow(
                    """SELECT status, metadata, selected_option_id, execution_status
                         FROM control_room_items
                        WHERE workspace_id=$1 AND item_id=$2""",
                    workspace_id,
                    winner["id"],
                )
            )
            return {**stored, "metadata": _metadata(stored["metadata"])}

        before = await state()
        assert before["status"] == "in_review"
        assert before["selected_option_id"] == "review"
        assert before["execution_status"] == "dry_run_validated"
        assert before["metadata"][DECISION_PROVENANCE_KEY] == provenance
        assert WORKFLOW_QUARANTINE_KEY not in before["metadata"]

        await persist_item_rows(conn, [loser_row], owner_scope_id=7)
        assert await state() == before

        await ensure_item_row(
            conn,
            {**loser_row, "status": "open"},
            terminal_statuses=("approved", "dismissed", "resolved"),
            owner_scope_id=7,
        )
        assert await state() == before
    finally:
        await conn.close()
