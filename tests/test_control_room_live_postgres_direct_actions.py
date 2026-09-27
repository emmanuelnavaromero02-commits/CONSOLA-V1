from __future__ import annotations

import asyncio
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import control_room_service
from app.services.control_room import (
    business_action_binding_producer,
    business_action_catalog,
    business_action_handle,
    business_decision_proposal,
    business_direct_action_authority,
    business_exception_approval,
    business_exception_resolution,
    business_state_refresh,
    experience_freshness,
)
from app.services.control_room.business_action_authority_policy import (
    DIRECT_ACTION_TEMPLATE_IDS,
)
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_command_item import (
    load_persisted_command_item,
)
from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.control_room.surface_snapshot import (
    SurfaceScope,
    SurfaceSnapshot,
    collect_surface_snapshot,
)
from app.services.db_scope import run_with_db_scope
from tests.control_room_action_authority_live import _new_user
from tests.test_control_room_live_postgres_workflows import _item, _rows
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


REASON = "Proveedor validado por auditoría interna"


@dataclass(frozen=True)
class DirectSeed:
    admin_dsn: str
    console_dsn: str
    tenant_id: str
    workspace_id: str
    maker: dict[str, Any]
    outsider: dict[str, Any]
    approve_id: str
    proposal_id: str


async def _seed(admin_dsn: str, console_dsn: str) -> DirectSeed:
    suffix = uuid.uuid4().hex[:10]
    conn = await asyncpg.connect(admin_dsn)
    try:
        tenant_id = str(
            await conn.fetchval(
                "INSERT INTO tenants(name, slug) VALUES ($1, $2) RETURNING id",
                f"Direct tenant {suffix}",
                f"direct-{suffix}",
            )
        )
        workspace_id = str(
            await conn.fetchval(
                "INSERT INTO workspaces(tenant_id, name) VALUES ($1, $2) RETURNING id",
                tenant_id,
                f"Direct workspace {suffix}",
            )
        )
        maker = await _new_user(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            workspace_role="workspace_admin",
            suffix=f"direct-maker-{suffix}",
        )
        outsider = await _new_user(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            workspace_role="workspace_admin",
            suffix=f"direct-outsider-{suffix}",
        )
        approve_id = f"direct-approve-{suffix}"
        proposal_id = f"direct-proposal-{suffix}"
        await persist_item_rows(
            conn,
            _rows(
                [
                    _item(approve_id, tenant_id, workspace_id),
                    _item(proposal_id, tenant_id, workspace_id),
                ],
                tenant_id,
                workspace_id,
                owner=maker["id"],
            ),
            owner_scope_id=maker["id"],
        )
    finally:
        await conn.close()
    return DirectSeed(
        admin_dsn,
        console_dsn,
        tenant_id,
        workspace_id,
        maker,
        outsider,
        approve_id,
        proposal_id,
    )


@pytest.fixture(scope="module")
def direct_seed(
    postgres_with_real_init_schema: str, omega_console_live_dsn: str
) -> DirectSeed:
    return asyncio.run(_seed(postgres_with_real_init_schema, omega_console_live_dsn))


async def _live_item(pool: asyncpg.Pool, seed: DirectSeed, item_id: str) -> dict:
    async def _factory():
        return pool

    item = await load_persisted_command_item(
        item_id,
        seed.maker,
        pool_factory=_factory,
        run_scoped=run_with_db_scope,
        item_statuses=control_room_service.ITEM_STATUSES,
        severity_weights=control_room_service.SEVERITY_WEIGHT,
    )
    assert item is not None
    return dict(item)


def _snapshot(seed: DirectSeed, *items: dict) -> SurfaceSnapshot:
    return SurfaceSnapshot(
        generated_at=datetime.now(UTC),
        scope=SurfaceScope(seed.tenant_id, seed.workspace_id),
        items=tuple(items),
        diagnostics=(),
        sources=(),
        installations=(),
    )


def _pool_patches(pool: asyncpg.Pool, snapshot: SurfaceSnapshot):
    pooled = AsyncMock(return_value=pool)
    return (
        patch.object(control_room_service.auth, "pool", new=pooled),
        patch.object(
            business_action_handle,
            "collect_surface_snapshot",
            new=AsyncMock(return_value=snapshot),
        ),
    )


async def _issue(
    seed: DirectSeed, pool: asyncpg.Pool, snapshot: SurfaceSnapshot, templates
) -> dict[str, dict[str, str]]:
    with patch.object(
        business_action_binding_producer.auth,
        "pool",
        new=AsyncMock(return_value=pool),
    ):
        issued = await business_action_binding_producer.issue_action_bindings(
            seed.maker, snapshot, enabled_template_ids=frozenset(templates)
        )
    return {
        item_id: {action.kind: action.action_handle for action in actions}
        for item_id, actions in issued.items()
    }


async def _token_counts(seed: DirectSeed) -> dict[str, int]:
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        rows = await conn.fetch(
            """SELECT status, count(*) AS total FROM control_room_action_tokens
                WHERE workspace_id = $1::uuid GROUP BY status""",
            seed.workspace_id,
        )
    finally:
        await conn.close()
    return {str(row["status"]): int(row["total"]) for row in rows}


async def _item_row(seed: DirectSeed, item_id: str) -> dict:
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        row = await conn.fetchrow(
            """SELECT status, decision_id, metadata, execution_status
                 FROM control_room_items
                WHERE workspace_id = $1::uuid AND item_id = $2""",
            seed.workspace_id,
            item_id,
        )
    finally:
        await conn.close()
    data = dict(row)
    if isinstance(data["metadata"], str):
        data["metadata"] = json.loads(data["metadata"])
    return data


async def _count(seed: DirectSeed, sql: str, *args: Any) -> int:
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        return int(await conn.fetchval(sql, *args))
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_migration_seeds_enabled_direct_templates_matching_the_registry(
    direct_seed: DirectSeed,
):
    pool = await asyncpg.create_pool(direct_seed.console_dsn, min_size=1, max_size=2)
    try:
        with patch.object(
            business_action_catalog.auth, "pool", new=AsyncMock(return_value=pool)
        ):
            enabled = await business_action_catalog.load_enabled_action_template_ids(
                direct_seed.maker
            )
    finally:
        await pool.close()
    assert DIRECT_ACTION_TEMPLATE_IDS <= enabled
    assert "create_followup_task" in enabled
    conn = await asyncpg.connect(direct_seed.admin_dsn)
    try:
        rows = await conn.fetch(
            """SELECT template_id, label, requires_approval, config
                 FROM control_room_action_templates
                WHERE template_id = ANY($1::text[])""",
            sorted(DIRECT_ACTION_TEMPLATE_IDS),
        )
        applied = await conn.fetchval(
            "SELECT count(*) FROM schema_migrations WHERE filename = $1",
            "99zzzzv_control_room_direct_action_templates.sql",
        )
    finally:
        await conn.close()
    assert applied == 1
    assert {row["template_id"]: row["label"] for row in rows} == {
        template_id: ACTION_TEMPLATES[template_id]["label"]
        for template_id in DIRECT_ACTION_TEMPLATE_IDS
    }
    assert all(row["requires_approval"] is False for row in rows)


async def _insert_binding(conn: asyncpg.Connection, seed: DirectSeed, template_id: str):
    now = datetime.now(UTC)
    await conn.execute(
        """
        INSERT INTO control_room_action_tokens (
            tenant_id, workspace_id, stage, subject_user_id, token_digest,
            binding_handle_nonce, item_id, template_id, binding_digest,
            evidence_digest, observation_fingerprint, contract_digest,
            target_digest, decision_digest, access_revision_digest,
            rbac_policy_digest, issued_at, expires_at
        ) VALUES (
            $1::uuid, $2::uuid, 'action_binding', $3, $4, $5, $6, $7,
            $8, $8, $8, $8, $8, $8, $8, $8, $9, $10
        )
        """,
        seed.tenant_id,
        seed.workspace_id,
        seed.maker["id"],
        secrets.token_bytes(32),
        secrets.token_bytes(32),
        f"shape-{uuid.uuid4().hex[:8]}",
        template_id,
        "0" * 64,
        now,
        now + timedelta(minutes=5),
    )


class _Rollback(Exception):
    pass


@pytest.mark.asyncio
async def test_binding_shape_accepts_only_known_templates(direct_seed: DirectSeed):
    conn = await asyncpg.connect(direct_seed.admin_dsn)
    try:
        for template_id in sorted(DIRECT_ACTION_TEMPLATE_IDS | {"create_followup_task"}):
            with pytest.raises(_Rollback):
                async with conn.transaction():
                    await _insert_binding(conn, direct_seed, template_id)
                    raise _Rollback
        for template_id in ("request_owner_review", "prepare_sap_review", "unknown"):
            with pytest.raises(asyncpg.CheckViolationError):
                async with conn.transaction():
                    await _insert_binding(conn, direct_seed, template_id)
        intent_check = await conn.fetchval(
            """SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conname = 'control_room_action_intents_template_chk'"""
        )
    finally:
        await conn.close()
    assert "create_followup_task" in intent_check
    assert "approve_exception" not in intent_check


@pytest.mark.asyncio
async def test_direct_actions_are_real_audited_atomic_and_reversible(
    direct_seed: DirectSeed,
):
    seed = direct_seed
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)
    try:
        approve_live = await _live_item(pool, seed, seed.approve_id)
        proposal_live = await _live_item(pool, seed, seed.proposal_id)
        snapshot = _snapshot(seed, approve_live, proposal_live)
        with patch.object(
            experience_freshness.auth, "pool", new=AsyncMock(return_value=pool)
        ):
            before = await experience_freshness.compute_experience_fingerprint(seed.maker)
        tokens_before = await _token_counts(seed)
        with patch.object(
            experience_freshness.auth, "pool", new=AsyncMock(return_value=pool)
        ):
            again = await experience_freshness.compute_experience_fingerprint(seed.maker)
        assert again.fingerprint == before.fingerprint
        assert await _token_counts(seed) == tokens_before

        handles = await _issue(
            seed,
            pool,
            snapshot,
            {"approve_exception", "create_decision_proposal", "open_in_studio"},
        )
        assert set(handles[seed.approve_id]) == {"exception_approval", "decision_proposal"}
        approve_handle = handles[seed.approve_id]["exception_approval"]
        repeat = await _issue(seed, pool, snapshot, {"approve_exception"})
        assert repeat[seed.approve_id]["exception_approval"] == approve_handle

        patches = _pool_patches(pool, snapshot)
        with patches[0], patches[1]:
            with pytest.raises(HTTPException) as foreign:
                await business_exception_approval.approve_exception(
                    seed.outsider, action_handle=approve_handle, reason=REASON
                )
            assert foreign.value.status_code == 404
            with pytest.raises(HTTPException) as crossed:
                await business_decision_proposal.create_decision_proposal(
                    seed.maker, action_handle=approve_handle
                )
            assert crossed.value.status_code == 404
            approved = await business_exception_approval.approve_exception(
                seed.maker,
                action_handle=approve_handle,
                reason=REASON,
                idempotency_key="approve-once",
            )
            replayed = await business_exception_approval.approve_exception(
                seed.maker,
                action_handle=approve_handle,
                reason=REASON,
                idempotency_key="approve-once",
            )
            with pytest.raises(HTTPException) as reused:
                await business_exception_approval.approve_exception(
                    seed.maker,
                    action_handle=approve_handle,
                    reason=REASON,
                    idempotency_key="other-key",
                )
        assert approved.status == replayed.status == "exception_approved"
        assert reused.value.status_code == 404

        row = await _item_row(seed, seed.approve_id)
        assert row["status"] == "dismissed"
        assert row["decision_id"] is None
        assert row["metadata"]["resolution"] == "exception_approved"
        assert row["metadata"]["resolution_actor_id"] == seed.maker["id"]
        assert row["metadata"]["resolution_reason"] == REASON
        assert row["metadata"]["resolution_at"]
        assert (
            await _count(
                seed,
                """SELECT count(*) FROM control_room_item_events
                    WHERE workspace_id = $1::uuid AND item_id = $2
                      AND event_type = 'exception_approved'""",
                seed.workspace_id,
                seed.approve_id,
            )
            == 1
        )
        assert (
            await _count(
                seed,
                """SELECT count(*) FROM audit_events
                    WHERE action = 'control_room.exception.approve'
                      AND resource_id = $1""",
                seed.approve_id,
            )
            == 1
        )
        assert (
            await _count(
                seed,
                """SELECT count(*) FROM control_room_action_tokens
                    WHERE workspace_id = $1::uuid AND item_id = $2
                      AND template_id = 'approve_exception'
                      AND status = 'consumed'
                      AND result_state = 'exception_approved'""",
                seed.workspace_id,
                seed.approve_id,
            )
            == 1
        )
        with patch.object(
            experience_freshness.auth, "pool", new=AsyncMock(return_value=pool)
        ):
            after = await experience_freshness.compute_experience_fingerprint(seed.maker)
        assert after.fingerprint != before.fingerprint

        with patch.object(
            business_exception_resolution.auth, "pool", new=AsyncMock(return_value=pool)
        ):
            approved = await business_exception_resolution.load_approved_exceptions(
                seed.maker
            )
        record = next(r for r in approved if r.item_id == seed.approve_id)
        assert record.resolution.reason == REASON
        assert record.resolution.actor_user_id == seed.maker["id"]
        stored = await _item_row(seed, seed.approve_id)
        assert stored["metadata"]["resolution_observation_fingerprint"] == (
            stored["metadata"]["business_eligibility_fingerprint"]
        )
        assert len(stored["metadata"]["resolution_evidence_digest"]) == 64
        with patch.object(
            business_action_binding_producer.auth,
            "pool",
            new=AsyncMock(return_value=pool),
        ):
            reopen_actions = await business_action_binding_producer.issue_reopen_bindings(
                seed.maker,
                [seed.approve_id],
                enabled_template_ids={"reopen_exception"},
            )
        reopen_handle = reopen_actions[seed.approve_id].action_handle
        no_live = _snapshot(seed)
        patches = _pool_patches(pool, no_live)
        with patches[0], patches[1]:
            reopened = await business_exception_approval.reopen_exception(
                seed.maker,
                action_handle=reopen_handle,
                reason="Revisar de nuevo",
            )
        assert reopened.status == "exception_reopened"
        row = await _item_row(seed, seed.approve_id)
        assert row["status"] == "open"
        assert not {
            "resolution",
            "resolution_actor_id",
            "resolution_reason",
            "resolution_at",
        } & set(row["metadata"])
        assert (
            await _count(
                seed,
                """SELECT count(*) FROM audit_events
                    WHERE action = 'control_room.exception.reopen'
                      AND resource_id = $1""",
                seed.approve_id,
            )
            == 1
        )

        proposal_handle = handles[seed.proposal_id]["decision_proposal"]
        patches = _pool_patches(pool, snapshot)
        with (
            patches[0],
            patches[1],
            patch.object(
                control_room_service,
                "_item_for_mutation",
                new=AsyncMock(return_value=proposal_live),
            ),
        ):
            proposed = await business_decision_proposal.create_decision_proposal(
                seed.maker, action_handle=proposal_handle, idempotency_key="propose"
            )
            proposed_again = await business_decision_proposal.create_decision_proposal(
                seed.maker, action_handle=proposal_handle, idempotency_key="propose"
            )
        assert proposed.status == "proposal_created"
        assert proposed_again.status == "proposal_exists"
        assert proposed_again.decision_id == proposed.decision_id
        assert proposed.href == f"/decisions?tab=consejo&propuesta={proposed.decision_id}"
        row = await _item_row(seed, seed.proposal_id)
        assert row["status"] == "decision_created"
        assert row["decision_id"] == proposed.decision_id
        conn = await asyncpg.connect(seed.admin_dsn)
        try:
            decision = await conn.fetchrow(
                """SELECT commitment_date, workspace_id::text AS workspace_id
                     FROM decisions WHERE id = $1""",
                proposed.decision_id,
            )
            today = await conn.fetchval("SELECT CURRENT_DATE")
            marker = await conn.fetchval(
                """SELECT count(*) FROM decision_actions
                    WHERE decision_id = $1
                      AND action_text = 'Decision creada desde Sala de Control'""",
                proposed.decision_id,
            )
        finally:
            await conn.close()
        assert decision["workspace_id"] == seed.workspace_id
        assert decision["commitment_date"] == today + timedelta(days=7)
        assert marker == 1
        assert (
            await _count(
                seed,
                """SELECT count(*) FROM audit_events
                    WHERE action = 'control_room.decision.create'
                      AND resource_id = $1""",
                seed.proposal_id,
            )
            == 1
        )
        with patch.object(
            business_direct_action_authority.auth, "pool", new=AsyncMock(return_value=pool)
        ):
            assert (
                await business_direct_action_authority.find_direct_action_replay(
                    seed.outsider,
                    action_handle=proposal_handle,
                    template_id="create_decision_proposal",
                    idempotency_key="propose",
                )
                is None
            )
    finally:
        await pool.close()


def _hcm_fetcher(seed: DirectSeed, detected_at: str):
    async def _fetch(dataset: str, _user: Any, _limit: int) -> list[dict[str, Any]]:
        if dataset != "employees_anomalies":
            return []
        return [
            {
                "tenant_id": seed.tenant_id,
                "workspace_id": seed.workspace_id,
                "pernr": "7001",
                "full_name": "Empleado de prueba",
                "anomaly_type": "terminated_but_active",
                "severity": "critical",
                "details": {"salary_monthly_usd": 4200},
                "detected_at": detected_at,
            }
        ]

    return _fetch


def _hcm_patches(pool: asyncpg.Pool, fetcher: Any):
    async def _installed(_user: Any) -> list[dict[str, Any]]:
        return [{"cartridge_id": "sap_hcm", "installation_status": "ready"}]

    original = control_room_service.refresh_dashboard_state
    return (
        patch.object(control_room_service.auth, "pool", new=AsyncMock(return_value=pool)),
        patch.object(control_room_service, "_installed_cartridges", new=_installed),
        patch.object(control_room_service, "query_dataset_rows", new=fetcher),
        patch.object(
            control_room_service,
            "refresh_dashboard_state",
            new=lambda user, **kwargs: original(user, fetcher=fetcher, **kwargs),
        ),
    )


async def _hcm_item(user: dict[str, Any]) -> dict[str, Any]:
    current = await collect_surface_snapshot(user)
    return next(
        dict(item)
        for item in current.items
        if item.get("source_dataset") == "employees_anomalies"
    )


@pytest.mark.asyncio
async def test_unpersisted_findings_need_the_audited_refresh_and_new_observations_lapse(
    direct_seed: DirectSeed,
):
    seed = direct_seed
    user = {**seed.maker, "email": seed.maker["email"]}
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)
    try:
        first = _hcm_patches(pool, _hcm_fetcher(seed, "2026-09-20T10:00:00Z"))
        with first[0], first[1], first[2], first[3]:
            live = await _hcm_item(user)
            item_id = str(live["id"])
            before = await business_action_binding_producer.issue_action_bindings(
                user,
                _snapshot(seed, live),
                enabled_template_ids={"approve_exception", "create_decision_proposal"},
            )
            assert [(a.kind, a.enabled, a.disabled_reason) for a in before[item_id]] == [
                ("exception_approval", False, "Actualiza los datos antes de continuar."),
                ("decision_proposal", False, "Actualiza los datos antes de continuar."),
            ]
            assert (
                await _count(
                    seed,
                    """SELECT count(*) FROM control_room_action_tokens
                        WHERE workspace_id = $1::uuid AND item_id = $2""",
                    seed.workspace_id,
                    item_id,
                )
                == 0
            )

            refreshed = await business_state_refresh.refresh_control_room_state(user)
            assert refreshed.status == "refreshed"
            row = await _item_row(seed, item_id)
            assert row["metadata"]["business_eligibility_fingerprint"]
            assert (
                await _count(
                    seed,
                    """SELECT count(*) FROM audit_events
                        WHERE action = 'control_room.state.refresh'
                          AND resource_id = $1""",
                    seed.workspace_id,
                )
                >= 1
            )

            live = await _hcm_item(user)
            after = await business_action_binding_producer.issue_action_bindings(
                user,
                _snapshot(seed, live),
                enabled_template_ids={"approve_exception", "create_decision_proposal"},
            )
            assert [(a.kind, a.enabled) for a in after[item_id]] == [
                ("exception_approval", True),
                ("decision_proposal", True),
            ]
            approve_handle = after[item_id][0].action_handle

            conn = await asyncpg.connect(seed.admin_dsn)
            try:
                await conn.execute(
                    """UPDATE control_room_action_tokens
                          SET issued_at = NOW() - INTERVAL '14 minutes',
                              expires_at = NOW() + INTERVAL '1 minute'
                        WHERE workspace_id = $1::uuid AND item_id = $2""",
                    seed.workspace_id,
                    item_id,
                )
            finally:
                await conn.close()
            rotated = await business_action_binding_producer.issue_action_bindings(
                user,
                _snapshot(seed, live),
                enabled_template_ids={"approve_exception"},
            )
            assert rotated[item_id][0].action_handle != approve_handle
            assert (
                await _count(
                    seed,
                    """SELECT count(*) FROM control_room_action_tokens
                        WHERE workspace_id = $1::uuid AND item_id = $2
                          AND template_id = 'approve_exception'""",
                    seed.workspace_id,
                    item_id,
                )
                == 1
            )
            with patch.object(
                business_action_handle,
                "collect_surface_snapshot",
                new=AsyncMock(return_value=_snapshot(seed, live)),
            ):
                await business_exception_approval.approve_exception(
                    user, action_handle=rotated[item_id][0].action_handle, reason=REASON
                )
            approved = await _item_row(seed, item_id)
            assert approved["status"] == "dismissed"

        drifted = _hcm_patches(pool, _hcm_fetcher(seed, "2026-09-24T10:00:00Z"))
        with drifted[0], drifted[1], drifted[2], drifted[3]:
            await business_state_refresh.refresh_control_room_state(user)
        lapsed = await _item_row(seed, item_id)
        assert lapsed["status"] == "open"
        conn = await asyncpg.connect(seed.admin_dsn)
        try:
            last_audit = await conn.fetchval(
                """SELECT metadata::text FROM audit_events
                    WHERE action = 'control_room.state.refresh'
                      AND resource_id = $1
                    ORDER BY id DESC LIMIT 1""",
                seed.workspace_id,
            )
        finally:
            await conn.close()
        assert json.loads(last_audit)["lapsed_exceptions"] == 1
        assert not any(key.startswith("resolution") for key in lapsed["metadata"])
        assert (
            await _count(
                seed,
                """SELECT count(*) FROM control_room_item_events
                    WHERE workspace_id = $1::uuid AND item_id = $2
                      AND event_type = 'exception_lapsed'""",
                seed.workspace_id,
                item_id,
            )
            == 1
        )
    finally:
        await pool.close()
