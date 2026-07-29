from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import auth
from app.services.control_room import business_action_intents
from app.services.control_room.business_action_intents import promote_action_handle
from app.services.control_room.business_action_execution_authority import (
    issue_execution_handle,
    reserve_execution,
)
from app.services.control_room.business_action_transitions import (
    approve_intent,
    claim_intent_for_approval,
)
from app.services.control_room.business_execution_precondition import dry_run_metadata
from tests.control_room_action_authority_dry_run import insert_authority_dry_run
from tests.control_room_action_authority_flow import issue_live_binding
from tests.control_room_action_authority_live import (
    AuthoritySeed,
    seed_authority,
    seed_authority_item,
    snapshot,
)
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


@pytest.fixture(scope="module")
def authority_seed(
    postgres_with_real_init_schema: str, omega_console_live_dsn: str
) -> AuthoritySeed:
    return asyncio.run(
        seed_authority(postgres_with_real_init_schema, omega_console_live_dsn)
    )


async def _pool(seed: AuthoritySeed) -> asyncpg.Pool:
    return await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=4)


async def _promote(seed: AuthoritySeed, pool: asyncpg.Pool, scope, handle: str):
    with (
        patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
        patch.object(
            business_action_intents,
            "collect_surface_snapshot",
            new=AsyncMock(return_value=snapshot(scope)),
        ),
    ):
        return await promote_action_handle(scope.maker, handle)


@pytest.mark.asyncio
async def test_promotion_without_persisted_dry_run_is_atomic_and_closed(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "missing-dry-run")
    pool = await _pool(seed)
    try:
        action = await issue_live_binding(seed, pool, scope)
        with pytest.raises(HTTPException) as blocked:
            await _promote(seed, pool, scope, action.action_handle)
        assert blocked.value.status_code == 409
        admin = await asyncpg.connect(seed.admin_dsn)
        try:
            counts = await admin.fetchrow(
                """
                SELECT
                  (SELECT count(*) FROM control_room_action_intents
                    WHERE workspace_id=$1::uuid AND item_id=$2) AS intents,
                  (SELECT count(*) FROM control_room_action_tokens
                    WHERE workspace_id=$1::uuid AND item_id=$2
                      AND stage='workflow') AS workflows,
                  (SELECT count(*) FROM control_room_action_intent_events e
                    JOIN control_room_action_intents i ON i.id=e.intent_id
                   WHERE i.workspace_id=$1::uuid AND i.item_id=$2) AS events
                """,
                scope.workspace_id,
                scope.item_id,
            )
        finally:
            await admin.close()
        assert dict(counts) == {"intents": 0, "workflows": 0, "events": 0}
    finally:
        await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("variant", "values"),
    (
        ("other-user", {"actor": "checker"}),
        ("failed", {"status": "dry_run_failed"}),
        ("not-ok", {"result": {"ok": False, "validated": True}}),
        ("not-validated", {"result": {"ok": True, "validated": False}}),
        ("incomplete", {"result": {"ok": True}}),
        ("string-flags", {"result": {"ok": "true", "validated": "true"}}),
        ("wrong-template", {"action_type": "prepare_hcm_access_review"}),
        ("wrong-contract", {"metadata": {"business_dry_run_contract": {}}}),
        ("other-workspace", {"scope": "second"}),
        ("other-item", {"scope": "first"}),
        ("other-decision", {"decision": "first"}),
        ("stale-contract", {"metadata": "first"}),
    ),
)
async def test_promotion_rejects_nonmatching_dry_run(
    authority_seed: AuthoritySeed, variant: str, values: dict
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, f"dry-{variant}")
    kwargs = dict(values)
    if kwargs.pop("actor", None) == "checker":
        kwargs["actor_id"] = scope.checker["id"]
    run_scope = {
        "second": seed.second,
        "first": seed.first,
    }.get(str(kwargs.pop("scope", "")), scope)
    if kwargs.pop("decision", None) == "first":
        kwargs["decision_id"] = int(seed.first.item["decision_id"])
    if kwargs.get("metadata") == "first":
        kwargs["metadata"] = dry_run_metadata(
            seed.first.item, template_id="create_followup_task"
        )
    await insert_authority_dry_run(seed, run_scope, **kwargs)
    pool = await _pool(seed)
    try:
        action = await issue_live_binding(seed, pool, scope)
        with pytest.raises(HTTPException) as blocked:
            await _promote(seed, pool, scope, action.action_handle)
        assert blocked.value.status_code == 409
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_exact_dry_run_is_linked_and_change_makes_intent_stale(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "dry-linked")
    run_id = await insert_authority_dry_run(seed, scope)
    pool = await _pool(seed)
    try:
        action = await issue_live_binding(seed, pool, scope)
        promoted = await _promote(seed, pool, scope, action.action_handle)
        admin = await asyncpg.connect(seed.admin_dsn)
        try:
            linked = await admin.fetchrow(
                """SELECT dry_run_action_run_id, dry_run_digest,
                          dry_run_evidence_digest
                     FROM control_room_action_intents WHERE id=$1::uuid""",
                promoted.intent_id,
            )
            assert linked["dry_run_action_run_id"] == run_id
            assert len(linked["dry_run_digest"]) == 64
            assert len(linked["dry_run_evidence_digest"]) == 64
            await admin.execute(
                "UPDATE action_runs SET dry_run_result='{}'::jsonb WHERE id=$1",
                run_id,
            )
        finally:
            await admin.close()
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            claim = await claim_intent_for_approval(scope.checker, promoted.intent_id)
            assert claim.state == "stale"
            assert claim.approval_handle is None
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_reservation_revalidates_the_same_linked_dry_run(
    authority_seed: AuthoritySeed,
):
    seed = authority_seed
    scope = await seed_authority_item(seed, seed.first, "dry-reserve")
    run_id = await insert_authority_dry_run(seed, scope)
    pool = await _pool(seed)
    try:
        action = await issue_live_binding(seed, pool, scope)
        promoted = await _promote(seed, pool, scope, action.action_handle)
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            claim = await claim_intent_for_approval(scope.checker, promoted.intent_id)
            await approve_intent(scope.checker, claim.approval_handle or "")
            execution = await issue_execution_handle(scope.checker, promoted.intent_id)
        admin = await asyncpg.connect(seed.admin_dsn)
        try:
            await admin.execute(
                "UPDATE action_runs SET status='blocked' WHERE id=$1", run_id
            )
        finally:
            await admin.close()
        with patch.object(auth, "pool", new=AsyncMock(return_value=pool)):
            result = await reserve_execution(
                scope.checker, execution.execution_handle or ""
            )
        assert result.state == "stale"
    finally:
        await pool.close()
