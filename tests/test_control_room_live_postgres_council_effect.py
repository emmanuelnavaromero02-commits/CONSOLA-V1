from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi import HTTPException

from app.services import audit_service, auth, control_room_service
from app.services.control_room import (
    business_action_binding_producer,
    business_action_handle,
    business_council_commands,
    business_council_view,
    business_decision_proposal,
)
from app.services.control_room.business_command_item import (
    load_persisted_command_item,
)
from app.services.control_room.business_council_actors import SYSTEM_MAKER
from app.services.control_room.business_followup_intent import (
    prepare_followup_intent,
)
from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.control_room.surface_snapshot import SurfaceScope, SurfaceSnapshot
from app.services.db_scope import run_with_db_scope
from tests.control_room_action_authority_live import (
    AuthorityScope,
    AuthoritySeed,
    _new_user,
    seed_authority,
    seed_authority_item,
)
from tests.test_control_room_live_postgres_workflows import _item, _rows
from tests.test_operational_rls_console_refinement import (
    omega_console_live_dsn,
    postgres_with_real_init_schema,
)


REASON = "La causa ya se corrigió en la fuente de datos"


@pytest.fixture(scope="module")
def council_seed(
    postgres_with_real_init_schema: str, omega_console_live_dsn: str
) -> AuthoritySeed:
    return asyncio.run(
        seed_authority(postgres_with_real_init_schema, omega_console_live_dsn)
    )


def _snapshot(scope: AuthorityScope, *items: dict[str, Any]) -> SurfaceSnapshot:
    from datetime import UTC, datetime

    return SurfaceSnapshot(
        generated_at=datetime.now(UTC),
        scope=SurfaceScope(scope.tenant_id, scope.workspace_id),
        items=tuple(items),
        diagnostics=(),
        sources=(),
        installations=(),
    )


def _patched(pool: asyncpg.Pool, snapshot: SurfaceSnapshot) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(patch.object(auth, "pool", new=AsyncMock(return_value=pool)))
    for module in (business_council_view, business_council_commands):
        stack.enter_context(
            patch.object(
                module,
                "collect_surface_snapshot",
                new=AsyncMock(return_value=snapshot),
            )
        )
    return stack


async def _fetch(seed: AuthoritySeed, sql: str, *args: Any) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        return [dict(row) for row in await conn.fetch(sql, *args)]
    finally:
        await conn.close()


async def _value(seed: AuthoritySeed, sql: str, *args: Any) -> Any:
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        return await conn.fetchval(sql, *args)
    finally:
        await conn.close()


def _metadata(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value or {})


async def _item_row(seed: AuthoritySeed, scope: AuthorityScope, item_id: str) -> dict:
    (row,) = await _fetch(
        seed,
        """SELECT status, decision_id, execution_status, metadata
             FROM control_room_items
            WHERE workspace_id = $1::uuid AND item_id = $2""",
        scope.workspace_id,
        item_id,
    )
    row["metadata"] = _metadata(row["metadata"])
    return row


async def _proposal(
    user: dict[str, Any], *, origin: str, decision_id: int | None = None
) -> Any:
    council = await business_council_view.build_action_council(user)
    matches = [
        proposal
        for proposal in council.proposals
        if proposal.origin == origin
        and (decision_id is None or proposal.decision_id == decision_id)
    ]
    assert len(matches) == 1, council.model_dump(mode="json")
    return matches[0]


async def _open_item(
    seed: AuthoritySeed, scope: AuthorityScope, pool: asyncpg.Pool, marker: str
) -> dict[str, Any]:
    item_id = f"council-open-{marker}-{uuid.uuid4().hex[:8]}"
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        await persist_item_rows(
            conn,
            _rows(
                [_item(item_id, scope.tenant_id, scope.workspace_id)],
                scope.tenant_id,
                scope.workspace_id,
                owner=scope.maker["id"],
            ),
            owner_scope_id=scope.maker["id"],
        )
    finally:
        await conn.close()

    async def _factory():
        return pool

    loaded = await load_persisted_command_item(
        item_id,
        scope.maker,
        pool_factory=_factory,
        run_scoped=run_with_db_scope,
        item_statuses=control_room_service.ITEM_STATUSES,
        severity_weights=control_room_service.SEVERITY_WEIGHT,
    )
    assert loaded is not None
    return dict(loaded)


@pytest.mark.asyncio
async def test_person_proposal_requires_another_person_and_completes_in_one_transaction(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    scope = seed.first
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)
    try:
        with _patched(pool, _snapshot(scope, scope.item)):
            intent_id = await prepare_followup_intent(scope.maker, scope.item_id)
            assert intent_id
            assert await prepare_followup_intent(scope.maker, scope.item_id) == intent_id
            decision_id = int(scope.item["decision_id"])

            own = await _proposal(scope.maker, origin="person", decision_id=decision_id)
            assert own.state == "needs_other_approver"
            assert own.authored_by_you is True
            assert own.can_approve is False
            assert own.disabled_reason == (
                "Requiere la aprobación de otra persona del equipo."
            )
            with pytest.raises(HTTPException) as maker_try:
                await business_council_commands.approve_council_proposal(
                    scope.maker, own.proposal_id, idempotency_key="maker-attempt-1"
                )
            assert maker_try.value.status_code == 403

            pending = await _proposal(
                scope.checker, origin="person", decision_id=decision_id
            )
            assert pending.state == "pending_approval"
            assert pending.can_approve is True and pending.disabled_reason is None
            assert pending.proposal_id != own.proposal_id
            with pytest.raises(HTTPException) as foreign_handle:
                await business_council_commands.approve_council_proposal(
                    scope.checker, own.proposal_id, idempotency_key="ajena-1"
                )
            assert foreign_handle.value.status_code == 404

            actions_before = await _value(
                seed,
                "SELECT count(*) FROM decision_actions WHERE decision_id = $1",
                decision_id,
            )
            original = audit_service.record_event

            async def _fail_effect_audit(*args: Any, **kwargs: Any) -> None:
                if kwargs.get("action") == "control_room.council.approve_and_follow_up":
                    raise RuntimeError("audit sink unavailable")
                await original(*args, **kwargs)

            with patch.object(audit_service, "record_event", new=_fail_effect_audit):
                with pytest.raises(RuntimeError):
                    await business_council_commands.approve_council_proposal(
                        scope.checker,
                        pending.proposal_id,
                        idempotency_key="approve-atomic-1",
                    )
            row = await _item_row(seed, scope, scope.item_id)
            assert row["status"] == "decision_created"
            assert row["execution_status"] == "not_started"
            assert await _value(
                seed,
                "SELECT state FROM control_room_action_intents WHERE id = $1::uuid",
                intent_id,
            ) == "pending_approval"
            assert await _value(
                seed,
                "SELECT count(*) FROM decision_actions WHERE decision_id = $1",
                decision_id,
            ) == actions_before

            approved = await business_council_commands.approve_council_proposal(
                scope.checker, pending.proposal_id, idempotency_key="approve-once-1"
            )
            replayed = await business_council_commands.approve_council_proposal(
                scope.checker, pending.proposal_id, idempotency_key="approve-once-1"
            )
            with pytest.raises(HTTPException) as reused:
                await business_council_commands.approve_council_proposal(
                    scope.checker, "f" * 64, idempotency_key="approve-once-1"
                )
            completed = await _proposal(
                scope.checker, origin="person", decision_id=decision_id
            )
        assert reused.value.status_code == 409
        assert approved.status == replayed.status == "approved_with_followup"
        assert approved.decision_id == replayed.decision_id == decision_id
        assert "No se modificó ningún sistema externo (ERP)" in approved.message
        assert completed.state == "completed"
        assert not (completed.can_approve or completed.can_discard or completed.can_renew)

        row = await _item_row(seed, scope, scope.item_id)
        assert row["status"] == "approved"
        assert row["execution_status"] == "executed"
        writeback = row["metadata"]["writeback_result"]
        assert writeback["origin"] == "control_room_council"
        assert writeback["maker"] == f"user:{scope.maker['id']}"
        assert writeback["checker_user_id"] == scope.checker["id"]

        actions = await _fetch(
            seed,
            """SELECT action_text, note, actor FROM decision_actions
                WHERE decision_id = $1 ORDER BY id""",
            decision_id,
        )
        assert len(actions) == actions_before + 2
        followup = actions[-1]
        assert followup["action_text"] == "Seguimiento operativo Control Room: Measured anomaly"
        assert followup["note"].endswith("No se escribio en ERP.")
        assert followup["actor"] == scope.checker["email"]
        assert actions[-2]["action_text"].startswith("Aprobacion en el Consejo de Acciones")

        intent = (
            await _fetch(
                seed,
                """SELECT state, maker_user_id, checker_user_id, executor_user_id
                     FROM control_room_action_intents WHERE id = $1::uuid""",
                intent_id,
            )
        )[0]
        assert intent == {
            "state": "completed",
            "maker_user_id": scope.maker["id"],
            "checker_user_id": scope.checker["id"],
            "executor_user_id": scope.checker["id"],
        }
        events = await _fetch(
            seed,
            """SELECT event_type, actor_user_id FROM control_room_action_intent_events
                WHERE intent_id = $1::uuid ORDER BY intent_version""",
            intent_id,
        )
        assert [event["event_type"] for event in events] == [
            "intent_created",
            "approval_claimed",
            "approved",
            "execution_reserved",
            "completed",
        ]
        assert {event["actor_user_id"] for event in events[1:]} == {scope.checker["id"]}
        audits = await _fetch(
            seed,
            """SELECT user_id, metadata FROM audit_events
                WHERE action = 'control_room.council.approve_and_follow_up'
                  AND resource_id = $1""",
            scope.item_id,
        )
        assert len(audits) == 1
        audit = _metadata(audits[0]["metadata"])
        assert audits[0]["user_id"] == scope.checker["id"]
        assert audit["maker"] == f"user:{scope.maker['id']}"
        assert audit["maker_user_id"] == scope.maker["id"]
        assert audit["checker_user_id"] == scope.checker["id"]
        assert audit["external_write"] is False
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_dual_role_person_cannot_approve_their_own_proposal(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    base = seed.second
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        dual = await _new_user(
            conn,
            tenant_id=base.tenant_id,
            workspace_id=base.workspace_id,
            workspace_role="control_room_approver",
            suffix=f"dual-{uuid.uuid4().hex[:8]}",
        )
        await conn.execute("UPDATE users SET role = 'admin' WHERE id = $1", dual["id"])
    finally:
        await conn.close()
    dual = {**dual, "role": "admin"}
    scope = AuthorityScope(
        base.tenant_id, base.workspace_id, base.item_id, dual, base.checker, base.item
    )
    scope = await seed_authority_item(seed, scope, "dual")
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=4)
    try:
        with _patched(pool, _snapshot(scope, scope.item)):
            assert await prepare_followup_intent(dual, scope.item_id)
            decision_id = int(scope.item["decision_id"])
            own = await _proposal(dual, origin="person", decision_id=decision_id)
            assert own.state == "needs_other_approver"
            assert own.can_approve is False
            with pytest.raises(HTTPException) as denied:
                await business_council_commands.approve_council_proposal(
                    dual, own.proposal_id, idempotency_key="dual-self-approval"
                )
        assert denied.value.status_code == 403
        row = await _item_row(seed, scope, scope.item_id)
        assert row["status"] == "decision_created"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_system_suggestion_is_made_by_the_system_and_checked_by_a_person(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    scope = seed.first
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)
    try:
        live = await _open_item(seed, scope, pool, "system")
        item_id = str(live["id"])
        with _patched(pool, _snapshot(scope, live)):
            suggestion = await _proposal(scope.checker, origin="system")
            assert suggestion.state == "pending_approval"
            assert suggestion.can_approve is True
            assert suggestion.decision_id is None
            admin_view = await _proposal(scope.maker, origin="system")
            assert admin_view.can_approve is True
            approved = await business_council_commands.approve_council_proposal(
                scope.checker, suggestion.proposal_id, idempotency_key="system-once-1"
            )
            again = await business_council_commands.approve_council_proposal(
                scope.checker, suggestion.proposal_id, idempotency_key="system-once-1"
            )
        assert again.decision_id == approved.decision_id
        decision = (
            await _fetch(
                seed,
                """SELECT created_by_id, created_by, status, workspace_id::text AS ws
                     FROM decisions WHERE id = $1""",
                approved.decision_id,
            )
        )[0]
        assert decision == {
            "created_by_id": None,
            "created_by": SYSTEM_MAKER,
            "status": "open",
            "ws": scope.workspace_id,
        }
        row = await _item_row(seed, scope, item_id)
        assert row["status"] == "approved"
        assert row["execution_status"] == "executed"
        assert row["decision_id"] == approved.decision_id
        audits = await _fetch(
            seed,
            """SELECT action, user_id, metadata FROM audit_events
                WHERE resource_id = $1 ORDER BY id""",
            item_id,
        )
        by_action = {audit["action"]: audit for audit in audits}
        created = _metadata(by_action["control_room.decision.create"]["metadata"])
        effect = _metadata(
            by_action["control_room.council.approve_and_follow_up"]["metadata"]
        )
        assert created["maker"] == effect["maker"] == SYSTEM_MAKER
        assert effect["maker_user_id"] is None
        assert effect["checker_user_id"] == scope.checker["id"]
        assert by_action["control_room.council.approve_and_follow_up"]["user_id"] == (
            scope.checker["id"]
        )
        assert await _value(
            seed,
            """SELECT count(*) FROM decision_actions
                WHERE decision_id = $1 AND actor = $2""",
            approved.decision_id,
            SYSTEM_MAKER,
        ) == 1
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_discard_needs_a_reason_and_closes_the_decision(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    scope = await seed_authority_item(seed, seed.first, "discard")
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)
    try:
        system_live = await _open_item(seed, scope, pool, "discard-system")
        with _patched(pool, _snapshot(scope, scope.item, system_live)):
            intent_id = await prepare_followup_intent(scope.maker, scope.item_id)
            decision_id = int(scope.item["decision_id"])
            person = await _proposal(scope.checker, origin="person", decision_id=decision_id)
            assert person.can_discard is True
            discarded = await business_council_commands.discard_council_proposal(
                scope.checker,
                person.proposal_id,
                reason=REASON,
                idempotency_key="discard-person-1",
            )
            system = await _proposal(scope.checker, origin="system")
            dropped = await business_council_commands.discard_council_proposal(
                scope.checker,
                system.proposal_id,
                reason=REASON,
                idempotency_key="discard-system-1",
            )
        assert discarded.decision_id == decision_id
        assert dropped.decision_id is None
        row = await _item_row(seed, scope, scope.item_id)
        assert row["status"] == "dismissed"
        decision = (
            await _fetch(
                seed,
                "SELECT status, closed_at, outcome FROM decisions WHERE id = $1",
                decision_id,
            )
        )[0]
        assert decision["status"] == "closed" and decision["closed_at"] is not None
        assert decision["outcome"] is None
        assert await _value(
            seed,
            """SELECT count(*) FROM decision_actions
                WHERE decision_id = $1 AND action_text = $2""",
            decision_id,
            f"Propuesta descartada en el Consejo: {REASON}",
        ) == 1
        assert await _value(
            seed,
            "SELECT state FROM control_room_action_intents WHERE id = $1::uuid",
            intent_id,
        ) == "rejected"
        system_row = await _item_row(seed, scope, str(system_live["id"]))
        assert system_row["status"] == "dismissed"
        assert system_row["decision_id"] is None
        audits = await _fetch(
            seed,
            """SELECT resource_id, metadata FROM audit_events
                WHERE action = 'control_room.council.discard'
                  AND resource_id = ANY($1::text[])""",
            [scope.item_id, str(system_live["id"])],
        )
        makers = {
            audit["resource_id"]: _metadata(audit["metadata"])["maker"]
            for audit in audits
        }
        assert makers == {
            scope.item_id: f"user:{scope.maker['id']}",
            str(system_live["id"]): SYSTEM_MAKER,
        }
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_expired_proposal_is_renewed_only_by_its_author(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    scope = await seed_authority_item(seed, seed.second, "renew")
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=4)
    try:
        with _patched(pool, _snapshot(scope, scope.item)):
            first = await prepare_followup_intent(scope.maker, scope.item_id)
            conn = await asyncpg.connect(seed.admin_dsn)
            try:
                await conn.execute(
                    """UPDATE control_room_action_intents
                          SET created_at = NOW() - INTERVAL '25 hours',
                              updated_at = NOW() - INTERVAL '25 hours',
                              expires_at = NOW() - INTERVAL '1 hour'
                        WHERE id = $1::uuid""",
                    first,
                )
            finally:
                await conn.close()
            decision_id = int(scope.item["decision_id"])
            expired = await _proposal(scope.maker, origin="person", decision_id=decision_id)
            assert expired.state == "expired"
            assert expired.can_renew is True
            assert expired.disabled_reason == (
                "La propuesta venció; su autor puede renovarla."
            )
            checker_view = await _proposal(
                scope.checker, origin="person", decision_id=decision_id
            )
            assert checker_view.can_renew is False and checker_view.can_approve is False
            with pytest.raises(HTTPException) as foreign:
                await business_council_commands.renew_council_proposal(
                    scope.checker, checker_view.proposal_id
                )
            assert foreign.value.status_code == 403
            renewed = await business_council_commands.renew_council_proposal(
                scope.maker, expired.proposal_id
            )
            again = await _proposal(scope.checker, origin="person", decision_id=decision_id)
        assert renewed.status == "renewed"
        assert again.state == "pending_approval" and again.can_approve is True
        assert await _value(
            seed,
            """SELECT count(*) FROM control_room_action_intents
                WHERE workspace_id = $1::uuid AND item_id = $2
                  AND state = 'pending_approval' AND expires_at > NOW()""",
            scope.workspace_id,
            scope.item_id,
        ) == 1
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_control_room_proposal_reaches_the_council_ready_for_another_person(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    scope = seed.second
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)
    try:
        live = await _open_item(seed, scope, pool, "from-control-room")
        current = _snapshot(scope, live)
        with patch.object(
            business_action_binding_producer.auth,
            "pool",
            new=AsyncMock(return_value=pool),
        ):
            issued = await business_action_binding_producer.issue_action_bindings(
                scope.maker, current, enabled_template_ids={"create_decision_proposal"}
            )
        (action,) = issued[str(live["id"])]
        with (
            _patched(pool, current),
            patch.object(
                business_action_handle,
                "collect_surface_snapshot",
                new=AsyncMock(return_value=current),
            ),
            patch.object(
                control_room_service,
                "_item_for_mutation",
                new=AsyncMock(return_value=live),
            ),
        ):
            created = await business_decision_proposal.create_decision_proposal(
                scope.maker, action_handle=action.action_handle, idempotency_key="cr-proposal-1"
            )
            proposal = await _proposal(
                scope.checker, origin="person", decision_id=created.decision_id
            )
        assert created.status == "proposal_created"
        assert proposal.state == "pending_approval" and proposal.can_approve is True
        assert await _value(
            seed,
            """SELECT count(*) FROM control_room_action_intents
                WHERE workspace_id = $1::uuid AND item_id = $2
                  AND maker_user_id = $3 AND state = 'pending_approval'""",
            scope.workspace_id,
            str(live["id"]),
            scope.maker["id"],
        ) == 1
        assert await _value(
            seed,
            """SELECT count(*) FROM action_runs
                WHERE workspace_id = $1::uuid AND item_id = $2
                  AND mode = 'dry_run' AND status = 'dry_run_completed'
                  AND action_intent_id IS NOT NULL""",
            scope.workspace_id,
            str(live["id"]),
        ) == 1
    finally:
        await pool.close()


async def _tenant_scope(seed: AuthoritySeed) -> tuple[AuthorityScope, dict[str, Any]]:
    base = seed.first
    suffix = uuid.uuid4().hex[:8]
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        tenant_admin = await _new_user(
            conn,
            tenant_id=base.tenant_id,
            workspace_id=base.workspace_id,
            workspace_role="tenant_admin",
            suffix=f"tenant-admin-{suffix}",
        )
        analyst = await _new_user(
            conn,
            tenant_id=base.tenant_id,
            workspace_id=base.workspace_id,
            workspace_role="analyst",
            suffix=f"analyst-{suffix}",
        )
    finally:
        await conn.close()
    scope = AuthorityScope(
        base.tenant_id,
        base.workspace_id,
        base.item_id,
        tenant_admin,
        base.checker,
        base.item,
    )
    return scope, analyst


@pytest.mark.asyncio
async def test_tenant_admin_approves_system_suggestions_but_never_their_own_proposal(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    scope, analyst = await _tenant_scope(seed)
    tenant_admin = scope.maker
    own = await seed_authority_item(seed, scope, "tenant-own")
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)
    try:
        live = await _open_item(seed, scope, pool, "tenant-system")
        with _patched(pool, _snapshot(scope, live, own.item)):
            assert await prepare_followup_intent(tenant_admin, own.item_id)
            decision_id = int(own.item["decision_id"])
            proposal = await _proposal(tenant_admin, origin="person", decision_id=decision_id)
            assert proposal.state == "needs_other_approver" and not proposal.can_approve
            with pytest.raises(HTTPException) as own_try:
                await business_council_commands.approve_council_proposal(
                    tenant_admin, proposal.proposal_id, idempotency_key="tenant-own-1"
                )
            assert own_try.value.status_code == 403

            suggestion = await _proposal(tenant_admin, origin="system")
            assert suggestion.can_approve is True
            analyst_view = await _proposal(analyst, origin="system")
            assert analyst_view.can_approve is False
            with pytest.raises(HTTPException) as analyst_try:
                await business_council_commands.approve_council_proposal(
                    analyst, analyst_view.proposal_id, idempotency_key="analyst-sys-1"
                )
            assert analyst_try.value.status_code == 403
            approved = await business_council_commands.approve_council_proposal(
                tenant_admin, suggestion.proposal_id, idempotency_key="tenant-sys-1"
            )

            other = await _proposal(scope.checker, origin="person", decision_id=decision_id)
            assert other.can_approve is True
            cross = await business_council_commands.approve_council_proposal(
                scope.checker, other.proposal_id, idempotency_key="checker-own-1"
            )
        assert cross.decision_id == decision_id
        row = await _item_row(seed, scope, str(live["id"]))
        assert row["status"] == "approved" and row["execution_status"] == "executed"
        assert row["metadata"]["writeback_result"]["maker"] == SYSTEM_MAKER
        assert row["metadata"]["writeback_result"]["checker_user_id"] == tenant_admin["id"]
        own_row = await _item_row(seed, scope, own.item_id)
        assert own_row["metadata"]["writeback_result"]["maker"] == f"user:{tenant_admin['id']}"
        assert own_row["metadata"]["writeback_result"]["checker_user_id"] == scope.checker["id"]
        events = await _fetch(
            seed,
            """SELECT event.authorization_permission, event.actor_workspace_role
                 FROM control_room_action_intent_events AS event
                 JOIN control_room_action_intents AS intent ON intent.id = event.intent_id
                WHERE intent.item_id = $1 AND event.event_type = 'approved'""",
            own.item_id,
        )
        assert events == [
            {
                "authorization_permission": "control_room.approve",
                "actor_workspace_role": "control_room_approver",
            }
        ]
        assert approved.decision_id != decision_id
    finally:
        await pool.close()

