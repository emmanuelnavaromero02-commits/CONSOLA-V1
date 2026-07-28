"""Real PostgreSQL proof that Copilot refresh persistence is atomic."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from app.services import copilot_context_persistence as persistence
from app.services import copilot_context_service as service
from tests.test_control_room_live_postgres_copilot_authority import (
    CopilotScope,
    copilot_scope,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


def _user(scope: CopilotScope) -> dict[str, object]:
    return {
        "id": 202,
        "email": "refresh-operator@example.com",
        "role": "tenant_admin",
        "active_tenant_id": scope.tenant_id,
        "active_workspace_id": scope.workspace_a,
    }


def _recommendation(fingerprint: str) -> list[dict[str, object]]:
    return [
        {
            "fingerprint": fingerprint,
            "severity": "warning",
            "category": "control_room",
            "title": "Atomic refresh recommendation",
            "body": "Review the current operational signal.",
            "evidence": {},
            "action_kind": "navigate",
            "required_permission": "operations.read",
        }
    ]


async def _read_state(
    scope: CopilotScope, fingerprints: list[str]
) -> dict[str, object]:
    conn = await asyncpg.connect(scope.admin_dsn)
    try:
        snapshot_rows = await conn.fetch(
            "SELECT workspace_id::text, count(*)::int AS total "
            "FROM copilot_context_snapshots "
            "WHERE workspace_id = ANY($1::uuid[]) GROUP BY workspace_id",
            [scope.workspace_a, scope.workspace_b],
        )
        recommendation_rows = await conn.fetch(
            "SELECT workspace_id::text, fingerprint, status "
            "FROM copilot_recommendations "
            "WHERE workspace_id = ANY($1::uuid[]) "
            "AND fingerprint = ANY($2::text[])",
            [scope.workspace_a, scope.workspace_b],
            fingerprints,
        )
        audit_rows = await conn.fetch(
            "SELECT user_id, email, ip, user_agent, status, "
            "metadata->>'workspace_id' AS workspace_id, "
            "metadata->>'generated_by' AS generated_by "
            "FROM audit_events WHERE action = 'copilot.context.refresh' "
            "AND metadata->>'tenant_id' = $1 ORDER BY created_at",
            scope.tenant_id,
        )
    finally:
        await conn.close()
    snapshots = {scope.workspace_a: 0, scope.workspace_b: 0}
    snapshots.update({row["workspace_id"]: int(row["total"]) for row in snapshot_rows})
    return {
        "snapshots": snapshots,
        "recommendations": {
            (row["workspace_id"], row["fingerprint"]): row["status"]
            for row in recommendation_rows
        },
        "audits": [dict(row) for row in audit_rows],
    }


async def _reactivate(scope: CopilotScope, fingerprint: str) -> None:
    conn = await asyncpg.connect(scope.admin_dsn)
    try:
        await conn.execute(
            "UPDATE copilot_recommendations SET status = 'active', resolved_at = NULL "
            "WHERE workspace_id = $1::uuid AND fingerprint = $2",
            scope.workspace_a,
            fingerprint,
        )
    finally:
        await conn.close()


async def _delete_refresh_audits(scope: CopilotScope) -> None:
    conn = await asyncpg.connect(scope.admin_dsn)
    try:
        await conn.execute(
            "DELETE FROM audit_events WHERE action = 'copilot.context.refresh' "
            "AND metadata->>'tenant_id' = $1",
            scope.tenant_id,
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_real_postgres_refresh_audit_and_state_share_one_transaction(
    copilot_scope: CopilotScope,
) -> None:
    pool = await asyncpg.create_pool(copilot_scope.console_dsn, min_size=1, max_size=2)
    user = _user(copilot_scope)
    empty = AsyncMock(return_value={})
    briefing = AsyncMock(return_value={"highlights": []})
    success_fingerprint = "live:atomic-success"
    rollback_fingerprint = "live:atomic-rollback"
    stale_fingerprint = "live:audit-rollback"
    fingerprints = [success_fingerprint, rollback_fingerprint, stale_fingerprint]
    try:
        with (
            patch.object(service.auth, "pool", AsyncMock(return_value=pool)),
            patch.object(service, "_load_operational_counts", empty),
            patch.object(service.control_room_service, "ops_summary", empty),
            patch.object(service.control_room_service, "agents_ops", empty),
            patch.object(
                service.control_room_service, "sap_successfactors_talent_kpis", empty
            ),
            patch.object(
                service.control_room_service,
                "sap_successfactors_talent_metadata_readiness",
                empty,
            ),
            patch.object(
                service.control_room_service,
                "sap_successfactors_talent_overview",
                empty,
            ),
            patch.object(service, "_proactive_briefing_for_context", briefing),
            patch.object(
                service,
                "build_recommendations_from_snapshot",
                return_value=_recommendation(success_fingerprint),
            ),
        ):
            result = await service.collect_workspace_context(
                user,
                persist=True,
                ip="127.0.0.1",
                user_agent="postgres-atomic-test",
            )

        assert result["persisted"] is True
        success = await _read_state(copilot_scope, fingerprints)
        assert success["snapshots"] == {
            copilot_scope.workspace_a: 1,
            copilot_scope.workspace_b: 0,
        }
        statuses = success["recommendations"]
        assert statuses[(copilot_scope.workspace_a, success_fingerprint)] == "active"
        assert statuses[(copilot_scope.workspace_a, stale_fingerprint)] == "superseded"
        assert statuses[(copilot_scope.workspace_b, stale_fingerprint)] == "active"
        assert len(success["audits"]) == 1
        audit = success["audits"][0]
        assert audit == {
            "user_id": user["id"],
            "email": user["email"],
            "ip": "127.0.0.1",
            "user_agent": "postgres-atomic-test",
            "status": "success",
            "workspace_id": copilot_scope.workspace_a,
            "generated_by": "manual",
        }

        await _reactivate(copilot_scope, stale_fingerprint)
        before_failure = await _read_state(copilot_scope, fingerprints)
        with (
            patch.object(service.auth, "pool", AsyncMock(return_value=pool)),
            patch.object(service, "_load_operational_counts", empty),
            patch.object(service.control_room_service, "ops_summary", empty),
            patch.object(service.control_room_service, "agents_ops", empty),
            patch.object(
                service.control_room_service, "sap_successfactors_talent_kpis", empty
            ),
            patch.object(
                service.control_room_service,
                "sap_successfactors_talent_metadata_readiness",
                empty,
            ),
            patch.object(
                service.control_room_service,
                "sap_successfactors_talent_overview",
                empty,
            ),
            patch.object(service, "_proactive_briefing_for_context", briefing),
            patch.object(
                service,
                "build_recommendations_from_snapshot",
                return_value=_recommendation(rollback_fingerprint),
            ),
            patch.object(
                persistence.audit_service,
                "record_event",
                AsyncMock(side_effect=RuntimeError("audit unavailable")),
            ),
        ):
            with pytest.raises(RuntimeError, match="audit unavailable"):
                await service.collect_workspace_context(user, persist=True)

        after_failure = await _read_state(copilot_scope, fingerprints)
        assert after_failure == before_failure
        assert (copilot_scope.workspace_a, rollback_fingerprint) not in after_failure[
            "recommendations"
        ]
    finally:
        await _delete_refresh_audits(copilot_scope)
        await pool.close()
