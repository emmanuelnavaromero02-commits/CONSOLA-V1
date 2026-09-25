from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import copilot_context_authority as authority
from app.services import copilot_context_service as service
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


@dataclass(frozen=True)
class CopilotScope:
    admin_dsn: str
    console_dsn: str
    tenant_id: str
    workspace_a: str
    workspace_b: str


async def _seed(admin_dsn: str, console_dsn: str) -> CopilotScope:
    suffix = uuid.uuid4().hex
    conn = await asyncpg.connect(admin_dsn)
    try:
        tenant_id = str(
            await conn.fetchval(
                "INSERT INTO tenants (name, slug) VALUES ($1, $2) RETURNING id",
                f"copilot-authority-{suffix}",
                f"copilot-authority-{suffix}",
            )
        )
        workspaces = []
        for label in ("A", "B"):
            workspaces.append(
                str(
                    await conn.fetchval(
                        "INSERT INTO workspaces (tenant_id, name) "
                        "VALUES ($1::uuid, $2) RETURNING id",
                        tenant_id,
                        f"Copilot authority {label} {suffix}",
                    )
                )
            )
        rows = [
            (f"live:blocked-{index}", "critical", "operations.read")
            for index in range(6)
        ]
        rows.extend(
            [
                ("live:allowed-a", "info", "monitor.read"),
                ("live:allowed-b", "info", "monitor.read"),
                ("live:ops-dismiss", "warning", "operations.read"),
                ("live:audit-rollback", "warning", "operations.read"),
            ]
        )
        for workspace_id in workspaces:
            await conn.executemany(
                """
                INSERT INTO copilot_recommendations (
                    tenant_id, workspace_id, fingerprint, severity, category,
                    title, body, required_permission
                )
                VALUES ($1::uuid, $2::uuid, $3, $4, 'control_room', $3, '', $5)
                """,
                [
                    (tenant_id, workspace_id, fingerprint, severity, required)
                    for fingerprint, severity, required in rows
                ],
            )
    finally:
        await conn.close()
    return CopilotScope(
        admin_dsn=admin_dsn,
        console_dsn=console_dsn,
        tenant_id=tenant_id,
        workspace_a=workspaces[0],
        workspace_b=workspaces[1],
    )


async def _cleanup(scope: CopilotScope) -> None:
    conn = await asyncpg.connect(scope.admin_dsn)
    try:
        await conn.execute(
            "DELETE FROM audit_events WHERE action = 'copilot.recommendation.dismiss' "
            "AND metadata->>'tenant_id' = $1",
            scope.tenant_id,
        )
        await conn.execute(
            "DELETE FROM workspaces WHERE id = ANY($1::uuid[])",
            [scope.workspace_a, scope.workspace_b],
        )
        await conn.execute("DELETE FROM tenants WHERE id = $1::uuid", scope.tenant_id)
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def copilot_scope(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> CopilotScope:
    scope = asyncio.run(_seed(postgres_with_real_init_schema, omega_console_live_dsn))
    yield scope
    asyncio.run(_cleanup(scope))


def _user(scope: CopilotScope, user_id: int, role: str) -> dict[str, object]:
    return {
        "id": user_id,
        "email": f"user-{user_id}@example.com",
        "role": role,
        "active_tenant_id": scope.tenant_id,
        "active_workspace_id": scope.workspace_a,
    }


async def _state(
    scope: CopilotScope,
) -> tuple[dict[str, int], dict[tuple[str, str], str]]:
    conn = await asyncpg.connect(scope.admin_dsn)
    try:
        snapshot_rows = await conn.fetch(
            "SELECT workspace_id::text, count(*)::int AS total "
            "FROM copilot_context_snapshots "
            "WHERE workspace_id = ANY($1::uuid[]) GROUP BY workspace_id",
            [scope.workspace_a, scope.workspace_b],
        )
        rows = await conn.fetch(
            "SELECT workspace_id::text, fingerprint, status "
            "FROM copilot_recommendations "
            "WHERE fingerprint = ANY($1::text[]) "
            "AND workspace_id = ANY($2::uuid[])",
            ["live:ops-dismiss", "live:audit-rollback"],
            [scope.workspace_a, scope.workspace_b],
        )
        snapshots = {scope.workspace_a: 0, scope.workspace_b: 0}
        snapshots.update(
            {row["workspace_id"]: int(row["total"]) for row in snapshot_rows}
        )
        return snapshots, {
            (row["workspace_id"], row["fingerprint"]): row["status"] for row in rows
        }
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_real_postgres_denials_are_read_only_and_filter_before_limit(
    copilot_scope: CopilotScope,
) -> None:
    pool = await asyncpg.create_pool(copilot_scope.console_dsn, min_size=1, max_size=2)
    viewer = _user(copilot_scope, 101, "viewer")
    operator = _user(copilot_scope, 102, "tenant_admin")
    writer = _user(copilot_scope, 103, "workspace_admin")
    try:
        pool_loader = AsyncMock(return_value=pool)
        with patch.object(authority.auth, "pool", pool_loader):
            visible = await service.list_recommendations(viewer, limit=2)
            assert {row["fingerprint"] for row in visible["recommendations"]} == {
                "live:allowed-a",
                "live:allowed-b",
            }

            before, _ = await _state(copilot_scope)
            with pytest.raises(HTTPException) as denied:
                await service.dismiss_recommendation(viewer, "live:allowed-a")
            assert denied.value.status_code == 403

            before_pool_calls = pool_loader.await_count
            collector = AsyncMock()
            with patch.object(service.control_room_service, "ops_summary", collector):
                with pytest.raises(HTTPException) as denied:
                    await service.collect_workspace_context(viewer, persist=True)
            assert denied.value.status_code == 403
            assert pool_loader.await_count == before_pool_calls
            collector.assert_not_awaited()

            with patch.object(
                authority.permissions,
                "has_permission",
                side_effect=lambda _user, name: name == "operations.read",
            ):
                with pytest.raises(HTTPException) as denied:
                    await service.collect_workspace_context(operator, persist=True)
            assert denied.value.status_code == 403
            assert pool_loader.await_count == before_pool_calls

            with pytest.raises(HTTPException) as denied:
                await service.dismiss_recommendation(writer, "live:ops-dismiss")
            assert denied.value.status_code == 403

            operator_rows = await service.list_recommendations(operator, limit=20)
            allowed = next(
                row
                for row in operator_rows["recommendations"]
                if row["fingerprint"] == "live:allowed-a"
            )
            assert allowed["status"] == "active"
            after, statuses = await _state(copilot_scope)
            assert after == before
            assert set(statuses.values()) == {"active"}
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_real_postgres_authorized_refresh_and_dismiss_stay_in_scope(
    copilot_scope: CopilotScope,
) -> None:
    pool = await asyncpg.create_pool(copilot_scope.console_dsn, min_size=1, max_size=2)
    operator = _user(copilot_scope, 102, "tenant_admin")
    empty = AsyncMock(return_value={})
    briefing = AsyncMock(return_value={"highlights": []})
    try:
        with (
            patch.object(authority.auth, "pool", AsyncMock(return_value=pool)),
            patch.object(service.control_room_service, "ops_summary", empty),
            patch.object(service.control_room_service, "agents_ops", empty),
            patch.object(
                service.control_room_service,
                "sap_successfactors_talent_kpis",
                empty,
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
        ):
            refreshed = await service.collect_workspace_context(operator, persist=True)
            dismissed = await service.dismiss_recommendation(
                operator,
                "live:ops-dismiss",
                ip="127.0.0.1",
                user_agent="postgres-test",
            )
            _, before_failure = await _state(copilot_scope)
            rollback_key = (copilot_scope.workspace_a, "live:audit-rollback")
            rollback_status = before_failure[rollback_key]
            failed_audit = AsyncMock(side_effect=RuntimeError("audit unavailable"))
            with patch.object(authority.audit_service, "record_event", failed_audit):
                with pytest.raises(RuntimeError, match="audit unavailable"):
                    await service.dismiss_recommendation(
                        operator, "live:audit-rollback"
                    )

        assert refreshed["persisted"] is True
        assert dismissed["status"] == "dismissed"
        snapshots, statuses = await _state(copilot_scope)
        assert snapshots == {
            copilot_scope.workspace_a: 1,
            copilot_scope.workspace_b: 0,
        }
        assert statuses[(copilot_scope.workspace_a, "live:ops-dismiss")] == "dismissed"
        assert statuses[(copilot_scope.workspace_b, "live:ops-dismiss")] == "active"
        assert statuses[rollback_key] == rollback_status
        assert statuses[(copilot_scope.workspace_b, "live:audit-rollback")] == "active"
        conn = await asyncpg.connect(copilot_scope.admin_dsn)
        try:
            audit = await conn.fetchrow(
                "SELECT user_id, email, ip, user_agent, "
                "metadata->>'workspace_id' AS audit_workspace_id, "
                "metadata->>'required_permission' AS required_permission "
                "FROM audit_events "
                "WHERE action = 'copilot.recommendation.dismiss' "
                "AND metadata->>'fingerprint' = 'live:ops-dismiss'"
            )
        finally:
            await conn.close()
        assert audit["user_id"] == operator["id"]
        assert audit["email"] == operator["email"]
        assert audit["ip"] == "127.0.0.1"
        assert audit["user_agent"] == "postgres-test"
        assert audit["audit_workspace_id"] == copilot_scope.workspace_a
        assert audit["required_permission"] == "operations.read"
    finally:
        await pool.close()
