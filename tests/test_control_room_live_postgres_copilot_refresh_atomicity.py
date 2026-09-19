"""Real PostgreSQL proof that Copilot refresh persistence is atomic."""

from __future__ import annotations

import uuid
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


# Ages in days of the rows seeded for the retention proof. Workspace "fresh" has
# history on both sides of the 14-day window; workspace "stale" only has rows
# older than the window, so the purge must keep exactly its newest one.
_FRESH_AGES = (30, 15, 13, 1, 0)
_STALE_AGES = (40, 30)


async def _seed_retention_workspaces(scope: CopilotScope) -> dict[str, object]:
    suffix = uuid.uuid4().hex
    conn = await asyncpg.connect(scope.admin_dsn)
    try:
        tenant_id = str(
            await conn.fetchval(
                "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
                f"copilot-retention-{suffix}",
                f"copilot-retention-{suffix}",
            )
        )
        workspaces: dict[str, str] = {}
        rows: dict[str, str] = {}
        for label, ages in (("fresh", _FRESH_AGES), ("stale", _STALE_AGES)):
            workspace_id = str(
                await conn.fetchval(
                    "INSERT INTO workspaces (tenant_id, name) "
                    "VALUES ($1::uuid, $2) RETURNING id",
                    tenant_id,
                    f"Copilot retention {label} {suffix}",
                )
            )
            workspaces[label] = workspace_id
            for age in ages:
                # Age 0 is a minute old so it never races the window edge.
                snapshot_id = await conn.fetchval(
                    "INSERT INTO copilot_context_snapshots "
                    "(tenant_id, workspace_id, generated_by, created_at) "
                    "VALUES ($1::uuid, $2::uuid, 'scheduler', "
                    "NOW() - ($3::int * INTERVAL '1 day') - INTERVAL '1 minute') "
                    "RETURNING id::text",
                    tenant_id,
                    workspace_id,
                    age,
                )
                rows[snapshot_id] = f"{label}:{age}"
        by_label = {label: snapshot_id for snapshot_id, label in rows.items()}
        await conn.executemany(
            "INSERT INTO copilot_recommendations (tenant_id, workspace_id, "
            "snapshot_id, fingerprint, severity, category, title, body) "
            "VALUES ($1::uuid, $2::uuid, $3::uuid, $4, 'info', 'control_room', "
            "$4, '')",
            [
                (tenant_id, workspaces["fresh"], by_label["fresh:30"], "live:expired"),
                (tenant_id, workspaces["fresh"], by_label["fresh:13"], "live:kept"),
            ],
        )
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "workspaces": workspaces, "rows": rows}


async def _retention_state(
    scope: CopilotScope, seeded: dict[str, object]
) -> tuple[dict[str, list[str]], dict[str, bool]]:
    workspaces = seeded["workspaces"]
    rows = seeded["rows"]
    conn = await asyncpg.connect(scope.admin_dsn)
    try:
        snapshot_rows = await conn.fetch(
            "SELECT id::text FROM copilot_context_snapshots "
            "WHERE workspace_id = ANY($1::uuid[])",
            list(workspaces.values()),
        )
        recommendation_rows = await conn.fetch(
            "SELECT fingerprint, snapshot_id IS NOT NULL AS linked "
            "FROM copilot_recommendations WHERE workspace_id = $1::uuid",
            workspaces["fresh"],
        )
    finally:
        await conn.close()
    kept: dict[str, list[str]] = {"fresh": [], "stale": []}
    for row in snapshot_rows:
        label, age = rows[row["id"]].split(":")
        kept[label].append(age)
    return (
        {label: sorted(ages, key=int) for label, ages in kept.items()},
        {row["fingerprint"]: row["linked"] for row in recommendation_rows},
    )


async def _cleanup_retention_workspaces(
    scope: CopilotScope, seeded: dict[str, object]
) -> None:
    conn = await asyncpg.connect(scope.admin_dsn)
    try:
        await conn.execute(
            "DELETE FROM workspaces WHERE id = ANY($1::uuid[])",
            list(seeded["workspaces"].values()),
        )
        await conn.execute(
            "DELETE FROM tenants WHERE id = $1::uuid", seeded["tenant_id"]
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_real_postgres_retention_purge_deletes_exactly_the_expired_history(
    copilot_scope: CopilotScope,
) -> None:
    """The purge runs unscoped as omega_console under FORCE RLS.

    It must delete only rows older than the window, never the newest row of a
    workspace, and detach recommendations that pointed at a purged snapshot.
    """

    seeded = await _seed_retention_workspaces(copilot_scope)
    pool = await asyncpg.create_pool(copilot_scope.console_dsn, min_size=1, max_size=2)
    try:
        result = await persistence.purge_expired_snapshots(pool, retention_days=14)
        kept, linked = await _retention_state(copilot_scope, seeded)

        assert result == {"status": "ok", "deleted": 3, "retention_days": 14}
        assert kept == {"fresh": ["0", "1", "13"], "stale": ["30"]}
        assert linked == {"live:expired": False, "live:kept": True}

        again = await persistence.purge_expired_snapshots(pool, retention_days=14)
        assert again == {"status": "ok", "deleted": 0, "retention_days": 14}
        assert (await _retention_state(copilot_scope, seeded))[0] == kept
    finally:
        await pool.close()
        await _cleanup_retention_workspaces(copilot_scope, seeded)
