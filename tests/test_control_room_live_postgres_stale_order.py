from __future__ import annotations

import copy
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


_TIME_FIELDS = {
    "as_of",
    "detected_at",
    "freshness_at",
    "generated_at",
    "observation_date",
    "observed_at",
    "period_key",
}


def _without_time(value):
    if isinstance(value, dict):
        return {
            key: _without_time(child)
            for key, child in value.items()
            if key not in _TIME_FIELDS
        }
    if isinstance(value, list):
        return [_without_time(child) for child in value]
    return copy.deepcopy(value)


def _measured_row(item: dict, tenant_id: str, workspace_id: str) -> dict:
    measured = {
        "kind": "anomaly",
        "metric_type": "count",
        "observed_value": item["observed_value"],
        "population_count": 10,
    }
    return {
        **_rows([item], tenant_id, workspace_id)[0],
        **measured,
        "metadata": measured,
    }


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
            (item, _measured_row(item, tenant_id, workspace_id)) for item in candidates
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


@pytest.mark.asyncio
async def test_live_modern_workflow_without_time_rejects_lower_fingerprint(
    postgres_with_real_init_schema: str,
) -> None:
    conn = await asyncpg.connect(postgres_with_real_init_schema)
    try:
        tenant_id, workspace_id = await _scope(conn)
        candidates = tuple(
            _item(
                "no-time-order",
                tenant_id,
                workspace_id,
                value=value,
                observed_at="2026-07-22T12:00:00Z",
            )
            for value in (10, 20)
        )
        generations = [
            (
                item,
                _measured_row(_without_time(item), tenant_id, workspace_id),
            )
            for item in candidates
        ]
        (loser, loser_row), (winner, winner_row) = sorted(
            generations,
            key=lambda generation: business_observation_order(generation[1]),
        )
        assert business_observation_order(loser_row) < business_observation_order(
            winner_row
        )

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
                      metadata=(metadata - $3::text) || $4::jsonb
                WHERE workspace_id=$1 AND item_id=$2""",
            workspace_id,
            winner["id"],
            "business_observation_order",
            json.dumps({DECISION_PROVENANCE_KEY: provenance}),
        )
        select_state = """SELECT title, status, metadata, selected_option_id,
                                   execution_status
                              FROM control_room_items
                             WHERE workspace_id=$1 AND item_id=$2"""
        before = dict(await conn.fetchrow(select_state, workspace_id, winner["id"]))
        before_metadata = _metadata(before["metadata"])
        assert "business_observation_order" not in before_metadata

        await persist_item_rows(conn, [loser_row], owner_scope_id=7)
        after = dict(await conn.fetchrow(select_state, workspace_id, winner["id"]))
        after_metadata = _metadata(after["metadata"])
        assert after["title"] == before["title"]
        assert after["status"] == before["status"]
        assert after["selected_option_id"] == before["selected_option_id"]
        assert after["execution_status"] == before["execution_status"]
        assert after_metadata[DECISION_PROVENANCE_KEY] == provenance
        assert after_metadata["business_observation_order"] == (
            business_observation_order(winner_row)
        )
        assert WORKFLOW_QUARANTINE_KEY not in after_metadata
    finally:
        await conn.close()
