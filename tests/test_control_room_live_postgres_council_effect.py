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
from app.services.control_room.business_decision_summary import (
    count_open_business_decisions,
)
from app.services.control_room.business_followup_intent import (
    prepare_followup_intent,
)
from app.services.control_room.business_item_persistence import persist_item_rows
from app.services.control_room.business_state_refresh import (
    refresh_control_room_state,
)
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
    _role_dsn,
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


async def _denials(
    seed: AuthoritySeed, action: str, item_id: str
) -> list[tuple[int, str, str | None]]:
    rows = await _fetch(
        seed,
        """SELECT user_id, status, metadata FROM audit_events
            WHERE action = $1 AND resource_id = $2 ORDER BY id""",
        action,
        item_id,
    )
    return [
        (row["user_id"], row["status"], _metadata(row["metadata"]).get("reason"))
        for row in rows
    ]


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
        and (
            proposal.decision_id == decision_id
            if decision_id is not None or origin == "system"
            else True
        )
    ]
    assert len(matches) == 1, council.model_dump(mode="json")
    return matches[0]


async def _open_item(
    seed: AuthoritySeed,
    scope: AuthorityScope,
    pool: asyncpg.Pool,
    marker: str,
    **extra: Any,
) -> dict[str, Any]:
    item_id = f"council-open-{marker}-{uuid.uuid4().hex[:8]}"
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        await persist_item_rows(
            conn,
            _rows(
                [{**_item(item_id, scope.tenant_id, scope.workspace_id), **extra}],
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
            denied_before = await _denials(
                seed, "control_room.council.approve.denied", scope.item_id
            )
            with pytest.raises(HTTPException) as maker_try:
                await business_council_commands.approve_council_proposal(
                    scope.maker, own.proposal_id, idempotency_key="maker-attempt-1"
                )
            assert maker_try.value.status_code == 403
            assert maker_try.value.detail["code"] == "self_approval"
            assert await _denials(
                seed, "control_room.council.approve.denied", scope.item_id
            ) == [*denied_before, (scope.maker["id"], "denied", "self_approval")]

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
        assert actions[-2]["action_text"].startswith("Aprobación en el Consejo de Acciones")

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
        assert denied.value.detail["code"] == "self_approval"
        assert await _denials(
            seed, "control_room.council.approve.denied", scope.item_id
        ) == [(dual["id"], "denied", "self_approval")]
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
                """SELECT created_by_id, created_by, assignee_id, status,
                          workspace_id::text AS ws
                     FROM decisions WHERE id = $1""",
                approved.decision_id,
            )
        )[0]
        assert decision == {
            "created_by_id": None,
            "created_by": SYSTEM_MAKER,
            "assignee_id": scope.checker["id"],
            "status": "open",
            "ws": scope.workspace_id,
        }
        assert await _value(
            seed,
            """SELECT count(*) FROM decision_actions
                WHERE decision_id = $1 AND action_text LIKE 'Aprobación en el Consejo%'""",
            approved.decision_id,
        ) == 1
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
        with _patched(pool, _snapshot(scope)):
            council = await business_council_view.build_action_council(scope.checker)
        done = next(p for p in council.proposals if p.decision_id == approved.decision_id)
        assert done.origin == "system" and done.state == "completed"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_platform_admin_outside_the_tenant_approves_without_becoming_assignee(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    scope = seed.first
    suffix = uuid.uuid4().hex[:8]
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        platform_id = int(
            await conn.fetchval(
                """INSERT INTO users(email, password_hash, role, tenant_id, is_active)
                   VALUES ($1, 'x', 'super_admin', NULL, TRUE) RETURNING id""",
                f"platform-{suffix}@example.test",
            )
        )
        await conn.execute(
            """INSERT INTO user_workspace_roles(user_id, workspace_id, role_id)
               SELECT $1, $2::uuid, id FROM roles WHERE name = 'workspace_admin'""",
            platform_id,
            scope.workspace_id,
        )
    finally:
        await conn.close()
    platform_admin = {
        "id": platform_id,
        "email": f"platform-{suffix}@example.test",
        "role": "super_admin",
        "workspace_role": "workspace_admin",
        "active_tenant_id": scope.tenant_id,
        "active_workspace_id": scope.workspace_id,
        "allowed_cartridges": ["sap_hcm"],
    }
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)
    try:
        live = await _open_item(seed, scope, pool, "platform-checker")
        with _patched(pool, _snapshot(scope, live)):
            suggestion = await _proposal(platform_admin, origin="system")
            assert suggestion.can_approve is True
            approved = await business_council_commands.approve_council_proposal(
                platform_admin, suggestion.proposal_id, idempotency_key="platform-1"
            )
        assert approved.status == "approved_with_followup"
        decision = (
            await _fetch(
                seed,
                "SELECT created_by, assignee_id, status FROM decisions WHERE id = $1",
                approved.decision_id,
            )
        )[0]
        assert decision == {
            "created_by": SYSTEM_MAKER,
            "assignee_id": None,
            "status": "open",
        }
        row = await _item_row(seed, scope, str(live["id"]))
        assert row["status"] == "approved"
        assert row["execution_status"] == "executed"
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
            own = await _proposal(scope.maker, origin="person", decision_id=decision_id)
            with pytest.raises(HTTPException) as self_discard:
                await business_council_commands.discard_council_proposal(
                    scope.maker,
                    own.proposal_id,
                    reason=REASON,
                    idempotency_key="discard-own-1",
                )
            assert self_discard.value.status_code == 403
            assert self_discard.value.detail["code"] == "self_discard"
            assert await _denials(
                seed, "control_room.council.discard.denied", scope.item_id
            ) == [(scope.maker["id"], "denied", "self_discard")]
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


async def _admin_execute(seed: AuthoritySeed, sql: str, *args: Any) -> None:
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_follow_up_kill_switch_blocks_system_and_person_approval(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    scope = await seed_authority_item(seed, seed.first, "kill-switch")
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)
    try:
        live = await _open_item(seed, scope, pool, "kill-switch")
        current = _snapshot(scope, live, scope.item)
        with _patched(pool, current):
            await prepare_followup_intent(scope.maker, scope.item_id)
            suggestion = await _proposal(scope.checker, origin="system")
            person = await _proposal(
                scope.checker, origin="person", decision_id=int(scope.item["decision_id"])
            )
            await _admin_execute(
                seed,
                """UPDATE control_room_action_templates SET enabled = FALSE
                    WHERE template_id = 'create_followup_task'""",
            )
            try:
                council = await business_council_view.build_action_council(scope.checker)
                assert not [
                    p
                    for p in council.proposals
                    if p.origin == "system" and p.state != "completed"
                ]
                blocked = next(p for p in council.proposals if p.proposal_id == person.proposal_id)
                assert blocked.can_approve is False
                assert blocked.disabled_reason == (
                    "Las tareas de seguimiento están desactivadas en este espacio de trabajo."
                )
                for proposal_id, key in (
                    (suggestion.proposal_id, "kill-system-1"),
                    (person.proposal_id, "kill-person-1"),
                ):
                    with pytest.raises(HTTPException) as refused:
                        await business_council_commands.approve_council_proposal(
                            scope.checker, proposal_id, idempotency_key=key
                        )
                    assert refused.value.status_code in {404, 409}
            finally:
                await _admin_execute(
                    seed,
                    """UPDATE control_room_action_templates SET enabled = TRUE
                        WHERE template_id = 'create_followup_task'""",
                )
        system_row = await _item_row(seed, scope, str(live["id"]))
        assert system_row["status"] == "open" and system_row["decision_id"] is None
        person_row = await _item_row(seed, scope, scope.item_id)
        assert person_row["status"] == "decision_created"
        assert person_row["execution_status"] == "not_started"
        assert await _value(
            seed,
            """SELECT state FROM control_room_action_intents
                WHERE workspace_id = $1::uuid AND item_id = $2
                ORDER BY created_at DESC LIMIT 1""",
            scope.workspace_id,
            scope.item_id,
        ) == "pending_approval"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_hidden_data_sources_cannot_see_approve_or_discard_proposals(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    scope = await seed_authority_item(seed, seed.second, "blind")
    blind = {**scope.checker, "allowed_cartridges": []}
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=4)
    try:
        with _patched(pool, _snapshot(scope, scope.item)):
            await prepare_followup_intent(scope.maker, scope.item_id)
            decision_id = int(scope.item["decision_id"])
            visible = await _proposal(scope.checker, origin="person", decision_id=decision_id)
            hidden = await business_council_view.build_action_council(blind)
            assert all(p.decision_id != decision_id for p in hidden.proposals)
            with pytest.raises(HTTPException) as approve:
                await business_council_commands.approve_council_proposal(
                    blind, visible.proposal_id, idempotency_key="oculta-1"
                )
            with pytest.raises(HTTPException) as discard:
                await business_council_commands.discard_council_proposal(
                    blind,
                    visible.proposal_id,
                    reason=REASON,
                    idempotency_key="blind-discard-1",
                )
        assert approve.value.status_code == discard.value.status_code == 404
        row = await _item_row(seed, scope, scope.item_id)
        assert row["status"] == "decision_created"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_a_checker_who_set_the_threshold_needs_another_person(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    scope = seed.second
    applied = {
        "cartridge_id": "sap_hcm",
        "anomaly_type": "council_threshold",
        "metric": f"gap_{uuid.uuid4().hex[:6]}",
        "warning_value": 1,
        "critical_value": None,
        "currency": "USD",
        "source": "workspace",
    }
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        threshold_id = await conn.fetchval(
            """INSERT INTO control_room_thresholds (
                   tenant_id, workspace_id, cartridge_id, anomaly_type, metric,
                   warning_value
               ) VALUES ($1::uuid, $2::uuid, 'sap_hcm', $3, $4, 1) RETURNING id""",
            scope.tenant_id,
            scope.workspace_id,
            applied["anomaly_type"],
            applied["metric"],
        )
        await conn.execute(
            """INSERT INTO audit_events (user_id, action, resource_type, resource_id, metadata)
               VALUES ($1, 'control_room.threshold.upsert', 'control_room_threshold', $2, '{}'::jsonb)""",
            scope.checker["id"],
            str(threshold_id),
        )
    finally:
        await conn.close()
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)
    try:
        live = await _open_item(
            seed, scope, pool, "threshold", thresholds_applied=[applied]
        )
        live["thresholds_applied"] = [applied]
        with _patched(pool, _snapshot(scope, live)):
            influenced = await _proposal(scope.checker, origin="system")
            assert influenced.state == "needs_other_approver"
            assert influenced.can_approve is False
            assert influenced.disabled_reason.startswith("Ajustaste un umbral")
            with pytest.raises(HTTPException) as refused:
                await business_council_commands.approve_council_proposal(
                    scope.checker, influenced.proposal_id, idempotency_key="self-threshold-1"
                )
            assert refused.value.status_code == 403
            assert refused.value.detail["code"] == "checker_influenced_proposal"
            assert await _denials(
                seed, "control_room.council.approve.denied", str(live["id"])
            ) == [(scope.checker["id"], "denied", "checker_influenced_proposal")]
            other = await _proposal(scope.maker, origin="system")
            assert other.can_approve is True
            approved = await business_council_commands.approve_council_proposal(
                scope.maker, other.proposal_id, idempotency_key="other-threshold-1"
            )
        assert approved.status == "approved_with_followup"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_author_renews_a_proposal_whose_pending_intent_drifted(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    scope = await seed_authority_item(seed, seed.first, "drift")
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=4)
    try:
        with _patched(pool, _snapshot(scope, scope.item)):
            first = await prepare_followup_intent(scope.maker, scope.item_id)
            await _admin_execute(
                seed,
                """UPDATE control_room_action_intents SET observation_fingerprint = $2
                    WHERE id = $1::uuid""",
                first,
                "0" * 64,
            )
            decision_id = int(scope.item["decision_id"])
            drifted = await _proposal(scope.maker, origin="person", decision_id=decision_id)
            assert drifted.state == "source_changed" and drifted.can_renew is True
            await business_council_commands.renew_council_proposal(
                scope.maker, drifted.proposal_id
            )
            ready = await _proposal(scope.checker, origin="person", decision_id=decision_id)
        assert ready.state == "pending_approval" and ready.can_approve is True
        states = await _fetch(
            seed,
            """SELECT state FROM control_room_action_intents
                WHERE workspace_id = $1::uuid AND item_id = $2 ORDER BY created_at""",
            scope.workspace_id,
            scope.item_id,
        )
        assert [row["state"] for row in states] == ["stale", "pending_approval"]
        stale_event = await _fetch(
            seed,
            """SELECT actor_user_id, authorization_permission
                 FROM control_room_action_intent_events
                WHERE intent_id = $1::uuid AND event_type = 'stale'""",
            first,
        )
        assert stale_event == [
            {"actor_user_id": scope.maker["id"], "authorization_permission": "control_room.write"}
        ]
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_concurrent_approve_and_discard_never_deadlock(council_seed: AuthoritySeed):
    seed = council_seed
    scope = await seed_authority_item(seed, seed.first, "race")
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        other = await _new_user(
            conn,
            tenant_id=scope.tenant_id,
            workspace_id=scope.workspace_id,
            workspace_role="workspace_admin",
            suffix=f"race-{uuid.uuid4().hex[:8]}",
        )
    finally:
        await conn.close()
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=2, max_size=8)
    try:
        with _patched(pool, _snapshot(scope, scope.item)):
            await prepare_followup_intent(scope.maker, scope.item_id)
            decision_id = int(scope.item["decision_id"])
            checker_view = await _proposal(scope.checker, origin="person", decision_id=decision_id)
            other_view = await _proposal(other, origin="person", decision_id=decision_id)
            results = await asyncio.gather(
                business_council_commands.approve_council_proposal(
                    scope.checker, checker_view.proposal_id, idempotency_key="race-approve-1"
                ),
                business_council_commands.discard_council_proposal(
                    other,
                    other_view.proposal_id,
                    reason=REASON,
                    idempotency_key="race-discard-1",
                ),
                return_exceptions=True,
            )
        successes = [result for result in results if not isinstance(result, Exception)]
        failures = [result for result in results if isinstance(result, Exception)]
        assert len(successes) == 1 and len(failures) == 1
        assert isinstance(failures[0], HTTPException)
        assert failures[0].status_code in {404, 409}
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_automations_summarise_each_airflow_run_of_the_workspace(
    council_seed: AuthoritySeed,
):
    from datetime import UTC, datetime, timedelta

    from app.services import pipeline_automations

    seed = council_seed
    mine, other = seed.first, seed.second
    dag_id = f"council_probe_{uuid.uuid4().hex[:6]}_extract_all"
    started = datetime(2026, 9, 26, 10, tzinfo=UTC)
    failed_run = f"manual__{uuid.uuid4().hex}"
    live_run = f"manual__{uuid.uuid4().hex}"
    rows = [
        (mine, failed_run, failed_run, "failed", started - timedelta(hours=2), "{}"),
        (mine, f"{failed_run}:employees", failed_run, "success", started - timedelta(hours=1), "{}"),
        (mine, f"{live_run}:employees", live_run, "running", started, "{}"),
        (mine, f"{live_run}:positions", live_run, "queued", started, "{}"),
        (mine, f"mirror-{uuid.uuid4().hex}", None, "running", started + timedelta(hours=1),
         '{"source": "extraction_runs_mirror"}'),
        (other, f"manual__{uuid.uuid4().hex}", None, "queued", started, "{}"),
    ]
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        await conn.execute(
            """INSERT INTO cartridge_dags (cartridge_id, dag_id, description, trigger)
               VALUES ('sap_hcm', $1, 'Extracción completa de prueba', 'on-demand')
               ON CONFLICT (cartridge_id, dag_id) DO NOTHING""",
            dag_id,
        )
        for scope, run_id, airflow_run, status, when, extra in rows:
            await conn.execute(
                """INSERT INTO pipeline_runs (
                       run_id, dag_id, cartridge_id, entity, status, started_at,
                       airflow_dag_run_id, extra, tenant_id, workspace_id
                   ) VALUES ($1, $2, 'sap_hcm', 'employees', $3, $4, $5, $6::jsonb,
                             $7::uuid, $8::uuid)""",
                run_id,
                dag_id,
                status,
                when,
                airflow_run,
                extra,
                scope.tenant_id,
                scope.workspace_id,
            )
    finally:
        await conn.close()

    async def _invoke(tool: str, _args: dict[str, Any], _user: Any) -> dict[str, Any]:
        assert tool == "airflow_list_dags"
        return {
            "dags": [
                {"dag_id": dag_id, "is_paused": True, "is_active": True, "schedule_kind": "manual"}
            ]
        }

    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=2)
    try:
        with patch.object(
            pipeline_automations.auth, "pool", new=AsyncMock(return_value=pool)
        ):
            response = await pipeline_automations.list_automations(
                mine.maker, invoke=_invoke
            )
            conn = await asyncpg.connect(seed.admin_dsn)
            try:
                await conn.execute(
                    "UPDATE pipeline_runs SET status = 'success' WHERE airflow_dag_run_id = $1",
                    live_run,
                )
            finally:
                await conn.close()
            settled = await pipeline_automations.list_automations(
                mine.maker, invoke=_invoke
            )
    finally:
        await pool.close()
    (probe,) = [a for a in response.automations if a.dag_id == dag_id]
    assert probe.runs_known is True
    assert probe.active_runs == 1
    assert probe.last_run is not None and probe.last_run.status == "running"
    assert probe.state_note_es == "Se activa automáticamente al pulsar Extraer"
    (after,) = [a for a in settled.automations if a.dag_id == dag_id]
    assert after.active_runs == 0
    assert after.last_run.status == "success"


@pytest.mark.asyncio
async def test_aggregate_row_outcome_wins_over_entity_rows(council_seed: AuthoritySeed):
    from datetime import UTC, datetime, timedelta

    from app.services import pipeline_automations

    seed = council_seed
    mine = seed.first
    dag_id = f"council_probe_{uuid.uuid4().hex[:6]}_extract_all"
    run = f"manual__{uuid.uuid4().hex}"
    started = datetime(2026, 9, 26, 9, tzinfo=UTC)
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        await conn.execute(
            """INSERT INTO cartridge_dags (cartridge_id, dag_id, description, trigger)
               VALUES ('sap_hcm', $1, 'Extracción completa de prueba', 'on-demand')
               ON CONFLICT (cartridge_id, dag_id) DO NOTHING""",
            dag_id,
        )
        for run_id, status, when in (
            (run, "failed", started - timedelta(minutes=5)),
            (f"{run}:employees", "success", started),
            (f"{run}:positions", "success", started),
        ):
            await conn.execute(
                """INSERT INTO pipeline_runs (
                       run_id, dag_id, cartridge_id, entity, status, started_at,
                       airflow_dag_run_id, tenant_id, workspace_id
                   ) VALUES ($1, $2, 'sap_hcm', 'employees', $3, $4, $5, $6::uuid, $7::uuid)""",
                run_id,
                dag_id,
                status,
                when,
                run,
                mine.tenant_id,
                mine.workspace_id,
            )
    finally:
        await conn.close()

    async def _invoke(_tool: str, _args: dict[str, Any], _user: Any) -> dict[str, Any]:
        return {"dags": [{"dag_id": dag_id, "is_paused": False, "schedule_kind": "manual"}]}

    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=2)
    try:
        with patch.object(
            pipeline_automations.auth, "pool", new=AsyncMock(return_value=pool)
        ):
            response = await pipeline_automations.list_automations(mine.maker, invoke=_invoke)
    finally:
        await pool.close()
    (probe,) = [a for a in response.automations if a.dag_id == dag_id]
    assert probe.last_run.status == "failed"
    assert probe.active_runs == 0


async def _delete_as_workspace_service(
    seed: AuthoritySeed, scope: AuthorityScope, decision_id: int
) -> str:
    conn = await asyncpg.connect(
        _role_dsn(seed.admin_dsn, "omega_workspace", "test_omega_workspace_password")
    )
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true), "
                "set_config('app.workspace_id', $2, true)",
                scope.tenant_id,
                scope.workspace_id,
            )
            return await conn.execute(
                "DELETE FROM decisions WHERE id = $1 AND workspace_id = $2::uuid",
                decision_id,
                scope.workspace_id,
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_database_refuses_deleting_council_decisions_from_any_role(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    scope = await seed_authority_item(seed, seed.second, "delete-guard")
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)
    try:
        with _patched(pool, _snapshot(scope, scope.item)):
            await prepare_followup_intent(scope.maker, scope.item_id)
            decision_id = int(scope.item["decision_id"])
            proposal = await _proposal(scope.checker, origin="person", decision_id=decision_id)
            await business_council_commands.approve_council_proposal(
                scope.checker, proposal.proposal_id, idempotency_key="delete-guard-1"
            )
    finally:
        await pool.close()
    plain = int(
        await _value(
            seed,
            """INSERT INTO decisions (title, workspace_id, created_by_id)
               VALUES ('Decisión manual', $1::uuid, $2) RETURNING id""",
            scope.workspace_id,
            scope.maker["id"],
        )
    )
    actions = "SELECT count(*) FROM decision_actions WHERE decision_id = $1"
    actions_before = await _value(seed, actions, decision_id)
    assert actions_before >= 2
    with pytest.raises(asyncpg.ForeignKeyViolationError) as refused:
        await _delete_as_workspace_service(seed, scope, decision_id)
    assert refused.value.constraint_name == "decisions_council_delete_protection"
    assert await _delete_as_workspace_service(seed, scope, plain) == "DELETE 1"
    assert await _value(seed, "SELECT count(*) FROM decisions WHERE id = $1", decision_id) == 1
    assert await _value(seed, actions, decision_id) == actions_before
    assert await _value(seed, "SELECT count(*) FROM decisions WHERE id = $1", plain) == 0


@pytest.mark.asyncio
async def test_disabling_a_threshold_keeps_its_author_from_approving_alone(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    scope = seed.first
    key = {
        "cartridge_id": "sap_hcm",
        "anomaly_type": "council_disabled",
        "metric": f"gap_{uuid.uuid4().hex[:6]}",
    }
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        threshold_id = await conn.fetchval(
            """INSERT INTO control_room_thresholds (
                   tenant_id, workspace_id, cartridge_id, anomaly_type, metric,
                   warning_value, enabled
               ) VALUES ($1::uuid, $2::uuid, $3, $4, $5, 1000, FALSE) RETURNING id""",
            scope.tenant_id,
            scope.workspace_id,
            key["cartridge_id"],
            key["anomaly_type"],
            key["metric"],
        )
        await conn.execute(
            """INSERT INTO audit_events (user_id, action, resource_type, resource_id, metadata)
               VALUES ($1, 'control_room.threshold.upsert', 'control_room_threshold', $2, '{}'::jsonb)""",
            scope.checker["id"],
            str(threshold_id),
        )
    finally:
        await conn.close()
    applied = {**key, "warning_value": 10, "critical_value": None, "currency": "USD", "source": "default"}
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=4)
    try:
        live = await _open_item(seed, scope, pool, "disabled-threshold", thresholds_applied=[applied])
        live["thresholds_applied"] = [applied]
        with _patched(pool, _snapshot(scope, live)):
            suggestion = await _proposal(scope.checker, origin="system")
            assert suggestion.state == "needs_other_approver"
            assert suggestion.disabled_reason.startswith("Ajustaste un umbral")
            with pytest.raises(HTTPException) as refused:
                await business_council_commands.approve_council_proposal(
                    scope.checker, suggestion.proposal_id, idempotency_key="disabled-th-1"
                )
        assert refused.value.status_code == 403
    finally:
        await pool.close()


TALENT_SIGNAL = {
    "signal_id": "talent_role_requirements_missing",
    "signal_type": "pipeline",
    "severity": "medium",
    "affected_count": 42,
    "title": "Requisitos de rol pendientes",
    "recommendation": "Validar Position y entidades de skills.",
    "status": "recommendation_only",
    "generated_at": "2026-07-05T03:27:32Z",
    "readiness_status": "partial",
    "source_row_count": 10,
    "materialized_at": "2026-07-05T03:27:32Z",
    "blockers": "[]",
}


async def _talent_workspace_admin(seed: AuthoritySeed) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:10]
    conn = await asyncpg.connect(seed.admin_dsn)
    try:
        tenant_id = str(
            await conn.fetchval(
                "INSERT INTO tenants(name, slug) VALUES ($1, $2) RETURNING id",
                f"Talent tenant {suffix}",
                f"talent-{suffix}",
            )
        )
        workspace_id = str(
            await conn.fetchval(
                "INSERT INTO workspaces(tenant_id, name) VALUES ($1, $2) RETURNING id",
                tenant_id,
                f"Talent workspace {suffix}",
            )
        )
        admin = await _new_user(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            workspace_role="workspace_admin",
            suffix=f"talent-admin-{suffix}",
        )
        await conn.execute(
            """INSERT INTO cartridge_installations (
                   id, tenant_id, workspace_id, cartridge_id, product_id, status,
                   current_step, install_fingerprint, created_by_id, ready_at
               ) VALUES ($1, $2::uuid, $3::uuid, 'sap_successfactors',
                         'sap_successfactors', 'ready', 'activated', $1, $4, NOW())""",
            f"talent-council-{suffix}",
            tenant_id,
            workspace_id,
            admin["id"],
        )
    finally:
        await conn.close()
    return {**admin, "allowed_cartridges": ["sap_successfactors"]}


@pytest.mark.asyncio
async def test_refreshed_talent_signal_reaches_the_council_and_becomes_a_decision(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    admin = await _talent_workspace_admin(seed)
    tenant_id = admin["active_tenant_id"]
    workspace_id = admin["active_workspace_id"]

    async def gold_rows(dataset: str, _user: Any, _limit: int = 1000) -> list[dict]:
        if dataset != "sap_successfactors_talent_signals":
            return []
        return [{**TALENT_SIGNAL, "tenant_id": tenant_id, "workspace_id": workspace_id}]

    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)
    try:
        with (
            patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
            patch(
                "app.services.intelligence.gold_fetcher.query_gold_dataset_rows",
                new=gold_rows,
            ),
        ):
            await refresh_control_room_state(admin)
            (talent,) = await _fetch(
                seed,
                """SELECT item_id, status, decision_id, selected_option_id, metadata
                     FROM control_room_items
                    WHERE workspace_id = $1::uuid
                      AND source_dataset = 'sap_successfactors_talent_signals'
                      AND item_kind = 'intelligence_signal'""",
                workspace_id,
            )
            metadata = _metadata(talent["metadata"])
            assert metadata["data_status"] == "gold_ready"
            assert metadata["business_eligibility_fingerprint"]
            assert talent["selected_option_id"] is None
            assert talent["status"] == "open" and talent["decision_id"] is None

            suggestion = await _proposal(admin, origin="system")
            assert suggestion.title == "Requisitos de rol pendientes"
            assert suggestion.state == "pending_approval" and suggestion.can_approve
            approved = await business_council_commands.approve_council_proposal(
                admin, suggestion.proposal_id, idempotency_key="talent-system-1"
            )
            council = await business_council_view.build_action_council(admin)
            assert await count_open_business_decisions(pool, admin) == 1
        done = next(p for p in council.proposals if p.decision_id == approved.decision_id)
        assert done.origin == "system" and done.state == "completed"
        decision = (
            await _fetch(
                seed,
                """SELECT created_by_id, created_by, assignee_id, status
                     FROM decisions WHERE id = $1 AND workspace_id = $2::uuid""",
                approved.decision_id,
                workspace_id,
            )
        )[0]
        assert decision == {
            "created_by_id": None,
            "created_by": SYSTEM_MAKER,
            "assignee_id": admin["id"],
            "status": "open",
        }
        assert await _value(
            seed,
            """SELECT count(*) FROM decision_actions
                WHERE decision_id = $1 AND action_text LIKE 'Aprobación en el Consejo%'""",
            approved.decision_id,
        ) == 1
        (row,) = await _fetch(
            seed,
            """SELECT status, execution_status, decision_id FROM control_room_items
                WHERE workspace_id = $1::uuid AND item_id = $2""",
            workspace_id,
            talent["item_id"],
        )
        assert row == {
            "status": "approved",
            "execution_status": "executed",
            "decision_id": approved.decision_id,
        }
        assert await _value(
            seed,
            """SELECT count(*) FROM audit_events
                WHERE action = 'control_room.state.refresh' AND resource_id = $1""",
            workspace_id,
        ) == 1
        assert await _value(
            seed,
            """SELECT count(*) FROM audit_events
                WHERE action = 'control_room.council.approve_and_follow_up'
                  AND resource_id = $1 AND user_id = $2""",
            talent["item_id"],
            admin["id"],
        ) == 1
    finally:
        await pool.close()


async def _talent_row(seed: AuthoritySeed, workspace_id: str) -> dict[str, Any]:
    (row,) = await _fetch(
        seed,
        """SELECT item_id, status, decision_id, execution_status, metadata
             FROM control_room_items
            WHERE workspace_id = $1::uuid
              AND source_dataset = 'sap_successfactors_talent_signals'
              AND item_kind = 'intelligence_signal'""",
        workspace_id,
    )
    row["metadata"] = _metadata(row["metadata"])
    return row


@pytest.mark.asyncio
async def test_approved_talent_signal_survives_the_next_refresh_and_stops_counting_as_open(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    admin = await _talent_workspace_admin(seed)
    tenant_id = admin["active_tenant_id"]
    workspace_id = admin["active_workspace_id"]

    async def gold_rows(dataset: str, _user: Any, _limit: int = 1000) -> list[dict]:
        if dataset != "sap_successfactors_talent_signals":
            return []
        return [{**TALENT_SIGNAL, "tenant_id": tenant_id, "workspace_id": workspace_id}]

    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)
    try:
        with (
            patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
            patch(
                "app.services.intelligence.gold_fetcher.query_gold_dataset_rows",
                new=gold_rows,
            ),
        ):
            await refresh_control_room_state(admin)
            before = await control_room_service.summary(admin)
            assert before["intelligence_signals"] == {
                "total": 1,
                "critical": 0,
                "high": 0,
                "medium": 1,
                "low": 0,
            }
            suggestion = await _proposal(admin, origin="system")
            approved = await business_council_commands.approve_council_proposal(
                admin, suggestion.proposal_id, idempotency_key="talent-refresh-1"
            )
            await refresh_control_room_state(admin)
            council = await business_council_view.build_action_council(admin)
            after = await control_room_service.summary(admin)
            open_decisions = await count_open_business_decisions(pool, admin)
        row = await _talent_row(seed, workspace_id)
        assert (row["status"], row["decision_id"], row["execution_status"]) == (
            "approved",
            approved.decision_id,
            "executed",
        )
        provenance = row["metadata"].get("decision_eligibility_provenance") or {}
        assert provenance.get("stage") == "executed"
        assert not (row["metadata"].get("workflow_quarantine") or {}).get("generations")
        assert not [
            p
            for p in council.proposals
            if p.origin == "system" and p.state == "pending_approval"
        ]
        assert open_decisions == 1
        assert after["open_decisions"] == 1
        assert after["intelligence_signals"]["total"] == 0
        assert await _value(
            seed, "SELECT count(*) FROM decisions WHERE workspace_id = $1::uuid", workspace_id
        ) == 1
    finally:
        await pool.close()


def _pending_system_titles(council: Any) -> list[str]:
    return [
        proposal.title
        for proposal in council.proposals
        if proposal.origin == "system" and proposal.state == "pending_approval"
    ]


@pytest.mark.asyncio
async def test_rematerialized_talent_gold_keeps_approval_until_its_value_changes(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    admin = await _talent_workspace_admin(seed)
    tenant_id = admin["active_tenant_id"]
    workspace_id = admin["active_workspace_id"]
    gold = {"at": "2026-10-01T03:00:00Z", "affected_count": 42}

    async def gold_rows(dataset: str, _user: Any, _limit: int = 1000) -> list[dict]:
        if dataset != "sap_successfactors_talent_signals":
            return []
        return [
            {
                **TALENT_SIGNAL,
                "affected_count": gold["affected_count"],
                "generated_at": gold["at"],
                "materialized_at": gold["at"],
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
            }
        ]

    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)
    try:
        with (
            patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
            patch(
                "app.services.intelligence.gold_fetcher.query_gold_dataset_rows",
                new=gold_rows,
            ),
        ):
            await refresh_control_room_state(admin)
            suggestion = await _proposal(admin, origin="system")
            approved = await business_council_commands.approve_council_proposal(
                admin, suggestion.proposal_id, idempotency_key="talent-remat-1"
            )

            gold["at"] = "2026-10-02T03:00:00Z"
            await refresh_control_room_state(admin)
            council = await business_council_view.build_action_council(admin)
            summary = await control_room_service.summary(admin)
            open_decisions = await count_open_business_decisions(pool, admin)
            stable = await _talent_row(seed, workspace_id)

            gold.update(at="2026-10-03T03:00:00Z", affected_count=43)
            await refresh_control_room_state(admin)
            changed_council = await business_council_view.build_action_council(admin)
            changed_summary = await control_room_service.summary(admin)
            changed = await _talent_row(seed, workspace_id)

        workflow = ("status", "decision_id", "execution_status")
        stable_metadata = stable["metadata"]
        assert tuple(stable[key] for key in workflow) == (
            "approved",
            approved.decision_id,
            "executed",
        )
        provenance = stable_metadata.get("decision_eligibility_provenance") or {}
        assert provenance.get("stage") == "executed"
        assert provenance.get("decision_id") == approved.decision_id
        quarantine = stable_metadata.get("workflow_quarantine") or {}
        assert not quarantine.get("generations")
        assert stable_metadata["detected_at"] == "2026-10-02T03:00:00Z"
        assert stable_metadata["details"]["materialized_at"] == "2026-10-02T03:00:00Z"
        assert _pending_system_titles(council) == []
        assert open_decisions == 1
        assert summary["open_decisions"] == 1
        assert summary["intelligence_signals"]["total"] == 0

        assert tuple(changed[key] for key in workflow) == (
            "open",
            None,
            "not_started",
        )
        quarantine = changed["metadata"].get("workflow_quarantine") or {}
        generations = quarantine.get("generations") or []
        assert [entry.get("reason") for entry in generations] == ["observation_changed"]
        assert generations[0].get("decision_id") == approved.decision_id
        title = "Requisitos de rol pendientes"
        assert _pending_system_titles(changed_council) == [title]
        assert changed_summary["intelligence_signals"]["total"] == 1
        decisions = await _value(
            seed,
            "SELECT count(*) FROM decisions WHERE workspace_id = $1::uuid",
            workspace_id,
        )
        assert decisions == 1
    finally:
        await pool.close()


def _talent_gold(admin: dict[str, Any], gold: dict[str, Any]) -> Any:
    async def gold_rows(dataset: str, _user: Any, _limit: int = 1000) -> list[dict]:
        if dataset != "sap_successfactors_talent_signals":
            return []
        return [
            {
                **TALENT_SIGNAL,
                "affected_count": gold["affected_count"],
                "generated_at": gold["at"],
                "materialized_at": gold["at"],
                "tenant_id": admin["active_tenant_id"],
                "workspace_id": admin["active_workspace_id"],
            }
        ]

    return gold_rows


@pytest.mark.parametrize("approve_changed_value", [False, True])
@pytest.mark.asyncio
async def test_talent_value_returning_to_a_quarantined_observation_can_be_approved(
    council_seed: AuthoritySeed, approve_changed_value: bool
):
    seed = council_seed
    admin = await _talent_workspace_admin(seed)
    workspace_id = admin["active_workspace_id"]
    gold = {"at": "2026-10-01T03:00:00Z", "affected_count": 42}
    title = "Requisitos de rol pendientes"
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)
    try:
        with (
            patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
            patch(
                "app.services.intelligence.gold_fetcher.query_gold_dataset_rows",
                new=_talent_gold(admin, gold),
            ),
        ):
            await refresh_control_room_state(admin)
            first = await business_council_commands.approve_council_proposal(
                admin,
                (await _proposal(admin, origin="system")).proposal_id,
                idempotency_key="talent-revert-1",
            )
            gold.update(at="2026-10-02T03:00:00Z", affected_count=43)
            await refresh_control_room_state(admin)
            if approve_changed_value:
                await business_council_commands.approve_council_proposal(
                    admin,
                    (await _proposal(admin, origin="system")).proposal_id,
                    idempotency_key="talent-revert-2",
                )
            gold.update(at="2026-10-03T03:00:00Z", affected_count=42)
            await refresh_control_room_state(admin)
            council = await business_council_view.build_action_council(admin)
            reverted = await _talent_row(seed, workspace_id)
            await control_room_service._item_for_mutation(
                reverted["item_id"], dict(admin)
            )
            again = await business_council_commands.approve_council_proposal(
                admin,
                (await _proposal(admin, origin="system")).proposal_id,
                idempotency_key="talent-revert-3",
            )
            gold["at"] = "2026-10-04T03:00:00Z"
            await refresh_control_room_state(admin)
            after = await business_council_view.build_action_council(admin)
            summary = await control_room_service.summary(admin)
        generations = reverted["metadata"]["workflow_quarantine"]["generations"]
        assert first.decision_id in [entry["decision_id"] for entry in generations]
        assert reverted["metadata"]["business_eligibility_fingerprint"] in [
            entry["fingerprint"] for entry in generations
        ]
        assert (reverted["status"], reverted["decision_id"]) == ("open", None)
        assert _pending_system_titles(council) == [title]
        assert again.decision_id not in (None, first.decision_id)
        row = await _talent_row(seed, workspace_id)
        assert (row["status"], row["decision_id"], row["execution_status"]) == (
            "approved",
            again.decision_id,
            "executed",
        )
        assert _pending_system_titles(after) == []
        assert summary["intelligence_signals"]["total"] == 0
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_pending_talent_proposal_keeps_its_id_until_the_observation_month_changes(
    council_seed: AuthoritySeed,
):
    seed = council_seed
    admin = await _talent_workspace_admin(seed)
    workspace_id = admin["active_workspace_id"]
    gold = {"at": "2026-08-01T03:00:00Z", "affected_count": 42}
    pool = await asyncpg.create_pool(seed.console_dsn, min_size=1, max_size=6)
    try:
        with (
            patch.object(auth, "pool", new=AsyncMock(return_value=pool)),
            patch(
                "app.services.intelligence.gold_fetcher.query_gold_dataset_rows",
                new=_talent_gold(admin, gold),
            ),
        ):
            await refresh_control_room_state(admin)
            before = await _proposal(admin, origin="system")
            gold["at"] = "2026-08-02T03:00:00Z"
            await refresh_control_room_state(admin)
            rerun = await _proposal(admin, origin="system")
            approved = await business_council_commands.approve_council_proposal(
                admin, before.proposal_id, idempotency_key="talent-pending-1"
            )
            gold["at"] = "2026-09-02T03:00:00Z"
            await refresh_control_room_state(admin)
            next_month = await _proposal(admin, origin="system")
            row = await _talent_row(seed, workspace_id)
        assert rerun.proposal_id == before.proposal_id
        assert approved.decision_id is not None
        assert next_month.proposal_id != before.proposal_id
        assert (row["status"], row["decision_id"]) == ("open", None)
        generations = row["metadata"]["workflow_quarantine"]["generations"]
        assert [entry["decision_id"] for entry in generations] == [approved.decision_id]
        assert [entry["reason"] for entry in generations] == ["observation_changed"]
    finally:
        await pool.close()
