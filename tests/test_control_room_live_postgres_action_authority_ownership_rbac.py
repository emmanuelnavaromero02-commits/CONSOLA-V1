from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from app.services import auth
from app.services.control_room.business_action_execution_authority import (
    issue_execution_handle,
    reserve_execution,
)
from app.services.control_room.business_action_transitions import (
    approve_intent,
    claim_intent_for_approval,
)
from app.services.permission_roles import ROLE_DEFINITIONS, ROLE_PERMISSIONS
from tests.control_room_action_authority_flow import promote_live_intent
from tests.control_room_action_authority_live import (
    AuthorityScope,
    AuthoritySeed,
    seed_authority,
    seed_authority_item,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


WRITER_ROLE = "control_room_owner_writer"


@pytest.fixture(scope="module")
def authority_seed(
    postgres_with_real_init_schema: str,
    omega_console_live_dsn: str,
) -> AuthoritySeed:
    return asyncio.run(
        seed_authority(postgres_with_real_init_schema, omega_console_live_dsn)
    )


async def _pool(seed: AuthoritySeed) -> asyncpg.Pool:
    return await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)


async def _set_workspace_role(
    seed: AuthoritySeed, scope: AuthorityScope, role: str | None
) -> None:
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        if role:
            await conn.execute(
                "INSERT INTO roles(name, description) VALUES ($1, $2) "
                "ON CONFLICT (name) DO NOTHING",
                role,
                "Test-only owner-scoped writer",
            )
        await conn.execute(
            "DELETE FROM user_workspace_roles WHERE user_id=$1 AND workspace_id=$2::uuid",
            scope.maker["id"],
            scope.workspace_id,
        )
        if role:
            await conn.execute(
                """INSERT INTO user_workspace_roles(user_id, workspace_id, role_id)
                   SELECT $1, $2::uuid, id FROM roles WHERE name=$3""",
                scope.maker["id"],
                scope.workspace_id,
                role,
            )
    finally:
        await conn.close()


async def _owner_scope(
    seed: AuthoritySeed, marker: str, monkeypatch: pytest.MonkeyPatch
) -> AuthorityScope:
    monkeypatch.setitem(
        ROLE_PERMISSIONS,
        WRITER_ROLE,
        {"workspace.access", "datasets.read", "control_room.write"},
    )
    monkeypatch.setitem(
        ROLE_DEFINITIONS,
        WRITER_ROLE,
        {
            "label": "Owner writer",
            "description": "Test-only owner-scoped writer",
            "assignable": False,
            "builtin": False,
            "legacy": False,
        },
    )
    scope = await seed_authority_item(seed, seed.first, marker)
    await _set_workspace_role(seed, scope, WRITER_ROLE)
    return replace(
        scope,
        maker={**scope.maker, "role": "user", "workspace_role": WRITER_ROLE},
    )


async def _reassign(seed: AuthoritySeed, scope: AuthorityScope) -> None:
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        await conn.execute(
            """UPDATE control_room_items SET owner_user_id=$3
                 WHERE workspace_id=$1::uuid AND item_id=$2""",
            scope.workspace_id,
            scope.item_id,
            scope.checker["id"],
        )
    finally:
        await conn.close()


async def _audit(seed: AuthoritySeed, intent_id: str) -> tuple[list[str], list[str]]:
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        events = await conn.fetch(
            "SELECT event_type FROM control_room_action_intent_events "
            "WHERE intent_id=$1::uuid ORDER BY intent_version",
            intent_id,
        )
        tokens = await conn.fetch(
            "SELECT stage FROM control_room_action_tokens "
            "WHERE intent_id=$1::uuid ORDER BY stage",
            intent_id,
        )
    finally:
        await conn.close()
    return [row["event_type"] for row in events], [row["stage"] for row in tokens]


@pytest.mark.asyncio
async def test_owner_scoped_reassignment_before_claim_becomes_stale(
    authority_seed: AuthoritySeed,
    monkeypatch: pytest.MonkeyPatch,
):
    seed = authority_seed
    scope = await _owner_scope(seed, "owner-claim", monkeypatch)
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
        await _reassign(seed, scope)
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            claim = await claim_intent_for_approval(scope.checker, promoted.intent_id)
        assert claim.state == "stale" and claim.approval_handle is None
        events, tokens = await _audit(seed, promoted.intent_id)
        assert events == ["intent_created", "stale"]
        assert "approval" not in tokens
    finally:
        await _set_workspace_role(seed, scope, "workspace_admin")
        await pool.close()


@pytest.mark.asyncio
async def test_owner_scoped_reassignment_before_reserve_never_reserves(
    authority_seed: AuthoritySeed,
    monkeypatch: pytest.MonkeyPatch,
):
    seed = authority_seed
    scope = await _owner_scope(seed, "owner-reserve", monkeypatch)
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            claim = await claim_intent_for_approval(scope.checker, promoted.intent_id)
            await approve_intent(scope.checker, claim.approval_handle or "")
            execution = await issue_execution_handle(scope.checker, promoted.intent_id)
        await _reassign(seed, scope)
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            result = await reserve_execution(
                scope.checker, execution.execution_handle or ""
            )
        assert result.state == "stale"
        events, _tokens = await _audit(seed, promoted.intent_id)
        assert "execution_reserved" not in events and events[-1] == "stale"
    finally:
        await _set_workspace_role(seed, scope, "workspace_admin")
        await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", (None, "analyst"))
async def test_current_db_membership_overrides_stale_session_claims(
    authority_seed: AuthoritySeed,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str | None,
):
    seed = authority_seed
    scope = await _owner_scope(seed, f"rbac-{replacement}", monkeypatch)
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
        await _set_workspace_role(seed, scope, replacement)
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            claim = await claim_intent_for_approval(scope.checker, promoted.intent_id)
        assert claim.state == "stale" and claim.approval_handle is None
        events, tokens = await _audit(seed, promoted.intent_id)
        assert events[-1] == "stale" and "approval" not in tokens
    finally:
        await _set_workspace_role(seed, scope, "workspace_admin")
        await pool.close()


@pytest.mark.asyncio
async def test_real_workspace_admin_retains_workspace_wide_owner_access(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "admin-owner")
    pool = await _pool(seed)
    try:
        promoted = await promote_live_intent(seed, pool, scope)
        await _reassign(seed, scope)
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            claim = await claim_intent_for_approval(scope.checker, promoted.intent_id)
        assert claim.state == "pending_approval" and claim.approval_handle
    finally:
        await pool.close()
