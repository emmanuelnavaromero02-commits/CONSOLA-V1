from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import HTTPException

from app.schemas.control_room_council import (
    CouncilApproveResponse,
    CouncilDiscardResponse,
    CouncilRenewResponse,
)
from app.services import audit_service, auth, control_room_service
from app.services.control_room.business_action_authority_policy import (
    actor_id,
    authority_scope,
    require_approve,
    require_write,
)
from app.services.control_room.business_action_authority_repository import (
    fetch_authoritative_row_for_update,
    fetch_direct_row_for_update,
    fetch_direct_rows,
)
from app.services.control_room.business_action_followup_effect import (
    complete_followup_effect,
    proposal_changed,
)
from app.services.control_room.business_action_mutations import (
    persist_status_transition,
    require_exact_count,
)
from app.services.control_room.business_action_revalidation import (
    actor_has_current_permission,
    revalidate_intent,
)
from app.services.control_room.business_action_transition_core import (
    lock_intent,
    transition_intent,
    transition_operation_digest,
)
from app.services.control_room.business_council_actors import (
    CHECKER_PERMISSIONS,
    SYSTEM_MAKER,
    CouncilMaker,
    require_council_checker,
    require_council_distinct_actors,
)
from app.services.control_room.business_council_view import (
    PENDING_STATUSES,
    PersonProposal,
    SystemSuggestion,
    read_person_proposals,
    system_candidate_items,
    system_suggestion_match,
    system_suggestions,
)
from app.services.control_room.business_decision_persistence import (
    create_and_link_decision,
)
from app.services.control_room.business_followup_intent import (
    prepare_followup_intent,
)
from app.services.control_room.surface_snapshot import collect_surface_snapshot
from app.services.db_scope import run_with_db_scope


DECISION_CREATE_AUDIT = "control_room.decision.create"
DISCARD_AUDIT = "control_room.council.discard"
RENEW_AUDIT = "control_room.council.renew"
DISCARD_ACTION_PREFIX = "Propuesta descartada en el Consejo: "
DISCARDED_EVENT = "council_discarded"
APPROVED_REPLAY_EVENT = "action_executed"
REPLAY_EVENTS = (APPROVED_REPLAY_EVENT, DISCARDED_EVENT)
REPLAY_SQL = """
SELECT event_type, metadata
  FROM control_room_item_events
 WHERE workspace_id = $1::uuid
   AND actor_id = $2
   AND event_type = ANY($3::text[])
   AND metadata ->> 'council_idempotency_digest' = $4
 ORDER BY id DESC
 LIMIT 1
"""
LOCK_OPEN_DECISION_SQL = """
SELECT id, created_by_id, status
  FROM decisions
 WHERE id = $1 AND workspace_id = $2
 FOR UPDATE
"""
CLOSE_DECISION_SQL = """
UPDATE decisions
   SET status = 'closed', closed_at = NOW()
 WHERE id = $1 AND workspace_id = $2 AND status = 'open'
"""
INSERT_DECISION_ACTION_SQL = """
INSERT INTO decision_actions (decision_id, action_text, note, actor)
VALUES ($1, $2, $3, $4)
RETURNING id
"""

Operation = Literal["approve", "discard"]


@dataclass(frozen=True)
class ReplayKeys:
    idempotency_digest: str
    proposal_digest: str
    lock_key: str = field(repr=False)

    @property
    def metadata(self) -> dict[str, str]:
        return {
            "council_idempotency_digest": self.idempotency_digest,
            "council_proposal_digest": self.proposal_digest,
        }


@dataclass(frozen=True)
class Outcome:
    kind: Literal["done", "missing", "stale"]
    decision_id: int | None = None


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def replay_keys(
    *, workspace_id: str, actor: int, operation: Operation, key: str, proposal_id: str
) -> ReplayKeys:
    idempotency = _digest("council", operation, workspace_id, str(actor), key)
    return ReplayKeys(
        idempotency_digest=idempotency,
        proposal_digest=_digest("council-proposal", proposal_id),
        lock_key=f"control-room-council:{idempotency}",
    )


def _not_found() -> HTTPException:
    return HTTPException(404, "council proposal not found")


def _source_changed() -> HTTPException:
    return HTTPException(
        409,
        {
            "code": "proposal_source_changed",
            "message": "council proposal source data changed",
        },
    )


def _same(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _scope_guard(
    tenant_id: str, workspace_id: str, scoped_tenant: str | None, scoped: str
) -> None:
    if str(scoped_tenant or "") != tenant_id or scoped != workspace_id:
        raise _not_found()


async def _replayed(
    conn: Any,
    *,
    workspace_id: str,
    actor: int,
    keys: ReplayKeys,
    event_type: str,
) -> Outcome | None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", keys.lock_key
    )
    row = await conn.fetchrow(
        REPLAY_SQL, workspace_id, actor, list(REPLAY_EVENTS), keys.idempotency_digest
    )
    if row is None:
        return None
    recorded = row.get("metadata")
    if isinstance(recorded, str):
        recorded = json.loads(recorded)
    if (
        not isinstance(recorded, Mapping)
        or str(row.get("event_type") or "") != event_type
        or recorded.get("council_proposal_digest") != keys.proposal_digest
    ):
        raise HTTPException(409, "idempotency key was used for another request")
    decision = recorded.get("decision_id")
    return Outcome("done", int(decision) if decision is not None else None)


async def _require_current(
    conn: Any, *, tenant_id: str, workspace_id: str, actor: int, permissions: Sequence[str]
) -> None:
    for permission in permissions:
        if not await actor_has_current_permission(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=actor,
            permission=permission,
        ):
            raise HTTPException(403, "action authority is unavailable")


async def _advance(
    conn: Any,
    intent: Mapping[str, Any],
    *,
    actor: int,
    event_type: str,
    operation: str,
    checker: int | None = None,
    executor: int | None = None,
) -> dict[str, Any]:
    return await transition_intent(
        conn,
        intent=intent,
        actor_user_id=actor,
        event_type=event_type,
        operation_digest=transition_operation_digest(
            intent, operation=operation, actor_user_id=actor
        ),
        checker_user_id=checker,
        executor_user_id=executor,
    )


def _match_person(
    proposals: Sequence[PersonProposal], proposal_id: str
) -> PersonProposal | None:
    return next((item for item in proposals if _same(item.handle, proposal_id)), None)


def _match_system(
    suggestions: Sequence[SystemSuggestion], proposal_id: str
) -> SystemSuggestion | None:
    return next((item for item in suggestions if _same(item.handle, proposal_id)), None)


async def _approve_person(
    conn: Any,
    *,
    user: Mapping[str, Any],
    proposal: PersonProposal,
    keys: ReplayKeys,
    ip: str | None,
    user_agent: str | None,
) -> Outcome:
    tenant_id, workspace_id = authority_scope(user)
    checker_id = actor_id(user)
    if proposal.intent is None:
        raise proposal_changed()
    intent = await lock_intent(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        intent_id=str(proposal.intent["id"]),
    )
    maker = CouncilMaker.person(intent.get("maker_user_id"))
    if maker.user_id != proposal.maker_user_id:
        raise proposal_changed()
    require_council_distinct_actors(maker, checker_id)
    await _require_current(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor=checker_id,
        permissions=CHECKER_PERMISSIONS,
    )
    existing_checker = intent.get("checker_user_id")
    if str(intent.get("state") or "") != "pending_approval" or (
        existing_checker is not None and int(existing_checker) != checker_id
    ):
        raise proposal_changed()
    try:
        contract = await revalidate_intent(conn, intent)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "action authority is temporarily unavailable") from None
    if contract is None or contract.decision_id != proposal.decision_id:
        await _advance(
            conn, intent, actor=checker_id, event_type="stale", operation="council_stale"
        )
        return Outcome("stale")
    current = intent
    if existing_checker is None:
        current = await _advance(
            conn,
            current,
            actor=checker_id,
            event_type="approval_claimed",
            operation="council_claim",
            checker=checker_id,
        )
    current = await _advance(
        conn, current, actor=checker_id, event_type="approved", operation="council_approve"
    )
    current = await _advance(
        conn,
        current,
        actor=checker_id,
        event_type="execution_reserved",
        operation="council_reserve",
        executor=checker_id,
    )
    row = await fetch_authoritative_row_for_update(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        item_id=proposal.item_id,
    )
    if row is None:
        raise proposal_changed()
    effect = await complete_followup_effect(
        conn,
        checker=user,
        row=row,
        item=contract.item,
        decision_id=contract.decision_id,
        maker=maker,
        intent_id=str(intent["id"]),
        replay=keys.metadata,
        ip=ip,
        user_agent=user_agent,
    )
    await _advance(
        conn, current, actor=checker_id, event_type="completed", operation="council_complete"
    )
    return Outcome("done", effect.decision_id)


async def _row_already_persisted(*_args: Any, **_kwargs: Any) -> None:
    return None


def _system_item(
    suggestion: SystemSuggestion, row: Mapping[str, Any]
) -> dict[str, Any]:
    live = dict(suggestion.live)
    return {
        **live,
        "id": suggestion.item_id,
        "owner_user_id": row.get("owner_user_id"),
        "description": str(live.get("description") or ""),
        "recommendation": str(live.get("recommendation") or ""),
        "entity_label": str(live.get("entity_label") or row.get("entity_label") or ""),
        "source_dataset": str(
            live.get("source_dataset") or row.get("source_dataset") or ""
        ),
        "cartridge": str(live.get("cartridge") or row.get("cartridge_id") or ""),
    }


async def _locked_suggestion(
    conn: Any,
    *,
    user: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    proposal_id: str,
) -> tuple[SystemSuggestion, dict[str, Any]] | None:
    tenant_id, workspace_id = authority_scope(user)
    rows = await fetch_direct_rows(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        item_ids=[str(item.get("id") or item.get("item_id") or "") for item in candidates],
    )
    match = _match_system(
        system_suggestions(
            candidates,
            rows,
            user=user,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        ),
        proposal_id,
    )
    if match is None:
        return None
    row = await fetch_direct_row_for_update(
        conn, tenant_id=tenant_id, workspace_id=workspace_id, item_id=match.item_id
    )
    if system_suggestion_match(
        match.live, row, tenant_id=tenant_id, workspace_id=workspace_id
    ) != (match.fingerprint, match.evidence_digest):
        raise proposal_changed()
    return match, dict(row or {})


async def _approve_system(
    conn: Any,
    *,
    user: Mapping[str, Any],
    suggestion: SystemSuggestion,
    row: Mapping[str, Any],
    keys: ReplayKeys,
    ip: str | None,
    user_agent: str | None,
) -> Outcome:
    tenant_id, workspace_id = authority_scope(user)
    checker_id = actor_id(user)
    maker = CouncilMaker.system()
    require_council_distinct_actors(maker, checker_id)
    await _require_current(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor=checker_id,
        permissions=CHECKER_PERMISSIONS,
    )
    item = _system_item(suggestion, row)
    decision = await create_and_link_decision(
        conn,
        user=dict(user),
        item=item,
        workspace_id=workspace_id,
        ensure_item_row=_row_already_persisted,
        record_item_event=control_room_service._record_item_event,
        system_maker=SYSTEM_MAKER,
    )
    decision_id = int(decision["id"]) if decision and decision.get("id") else 0
    if decision_id <= 0:
        raise proposal_changed()
    linked = await fetch_authoritative_row_for_update(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        item_id=suggestion.item_id,
    )
    if linked is None:
        raise proposal_changed()
    await audit_service.record_event(
        connection=conn,
        user_id=checker_id,
        email=user.get("email"),
        action=DECISION_CREATE_AUDIT,
        resource_type="control_room_item",
        resource_id=suggestion.item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={
            "decision_id": decision_id,
            "maker": maker.label,
            "checker_user_id": checker_id,
            "origin": "control_room_council",
        },
        critical=True,
    )
    effect = await complete_followup_effect(
        conn,
        checker=user,
        row=linked,
        item={**item, "decision_id": decision_id, "status": "decision_created"},
        decision_id=decision_id,
        maker=maker,
        replay=keys.metadata,
        ip=ip,
        user_agent=user_agent,
    )
    return Outcome("done", effect.decision_id)


async def approve_council_proposal(
    user: Mapping[str, Any],
    proposal_id: str,
    *,
    idempotency_key: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> CouncilApproveResponse:
    checker_id = require_council_checker(user)
    tenant_id, workspace_id = authority_scope(user)
    keys = replay_keys(
        workspace_id=workspace_id,
        actor=checker_id,
        operation="approve",
        key=idempotency_key,
        proposal_id=proposal_id,
    )
    pool = await auth.pool()

    async def _person(conn: Any, scoped_tenant: str | None, scoped: str) -> Outcome:
        _scope_guard(tenant_id, workspace_id, scoped_tenant, scoped)
        replayed = await _replayed(
            conn,
            workspace_id=workspace_id,
            actor=checker_id,
            keys=keys,
            event_type=APPROVED_REPLAY_EVENT,
        )
        if replayed is not None:
            return replayed
        proposals = await read_person_proposals(
            conn,
            user=user,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            statuses=PENDING_STATUSES,
        )
        match = _match_person(proposals, proposal_id)
        if match is None:
            return Outcome("missing")
        return await _approve_person(
            conn, user=user, proposal=match, keys=keys, ip=ip, user_agent=user_agent
        )

    outcome = await run_with_db_scope(pool, dict(user), _person)
    if outcome.kind == "stale":
        raise _source_changed()
    if outcome.kind == "missing":
        snapshot = await collect_surface_snapshot(user)
        candidates = system_candidate_items(snapshot)

        async def _system(conn: Any, scoped_tenant: str | None, scoped: str) -> Outcome:
            _scope_guard(tenant_id, workspace_id, scoped_tenant, scoped)
            replayed = await _replayed(
                conn,
                workspace_id=workspace_id,
                actor=checker_id,
                keys=keys,
                event_type=APPROVED_REPLAY_EVENT,
            )
            if replayed is not None:
                return replayed
            located = await _locked_suggestion(
                conn, user=user, candidates=candidates, proposal_id=proposal_id
            )
            if located is None:
                return Outcome("missing")
            suggestion, row = located
            return await _approve_system(
                conn,
                user=user,
                suggestion=suggestion,
                row=row,
                keys=keys,
                ip=ip,
                user_agent=user_agent,
            )

        outcome = await run_with_db_scope(pool, dict(user), _system)
    if outcome.kind != "done" or outcome.decision_id is None:
        raise _not_found()
    return CouncilApproveResponse(decision_id=outcome.decision_id)


async def _discard_transition(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item_id: str,
    owner_user_id: Any,
    reason: str,
    workspace_id: str,
) -> None:
    await persist_status_transition(
        conn,
        user=user,
        item={"id": item_id, "owner_user_id": owner_user_id},
        workspace_id=workspace_id,
        target_status="dismissed",
        event_type="dismissed",
        reason=reason,
        ensure_item_row=control_room_service._ensure_item_row,
    )


async def _discard_person(
    conn: Any,
    *,
    user: Mapping[str, Any],
    proposal: PersonProposal,
    reason: str,
    keys: ReplayKeys,
    ip: str | None,
    user_agent: str | None,
) -> Outcome:
    tenant_id, workspace_id = authority_scope(user)
    checker_id = actor_id(user)
    if proposal.maker_user_id is None:
        raise proposal_changed()
    maker = CouncilMaker.person(proposal.maker_user_id)
    require_council_distinct_actors(maker, checker_id)
    await _require_current(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor=checker_id,
        permissions=("control_room.approve",),
    )
    row = await fetch_direct_row_for_update(
        conn, tenant_id=tenant_id, workspace_id=workspace_id, item_id=proposal.item_id
    )
    decision = await conn.fetchrow(
        LOCK_OPEN_DECISION_SQL, proposal.decision_id, workspace_id
    )
    if (
        row is None
        or str(row.get("status") or "") != "decision_created"
        or row.get("decision_id") is None
        or int(row["decision_id"]) != proposal.decision_id
        or decision is None
        or str(decision.get("status") or "") != "open"
    ):
        raise proposal_changed()
    intent_id = None
    if proposal.intent is not None and str(proposal.intent.get("state")) == (
        "pending_approval"
    ):
        try:
            intent = await lock_intent(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                intent_id=str(proposal.intent["id"]),
            )
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            intent = None
        if intent is not None and str(intent.get("state")) == "pending_approval":
            existing_checker = intent.get("checker_user_id")
            if existing_checker is not None and int(existing_checker) != checker_id:
                raise proposal_changed()
            current = intent
            if existing_checker is None:
                current = await _advance(
                    conn,
                    current,
                    actor=checker_id,
                    event_type="approval_claimed",
                    operation="council_claim",
                    checker=checker_id,
                )
            await _advance(
                conn,
                current,
                actor=checker_id,
                event_type="rejected",
                operation="council_reject",
            )
            intent_id = str(intent["id"])
    await _discard_transition(
        conn,
        user=user,
        item_id=proposal.item_id,
        owner_user_id=row.get("owner_user_id"),
        reason=reason,
        workspace_id=workspace_id,
    )
    result = await conn.execute(CLOSE_DECISION_SQL, proposal.decision_id, workspace_id)
    require_exact_count(result, "UPDATE")
    await conn.fetchval(
        INSERT_DECISION_ACTION_SQL,
        proposal.decision_id,
        DISCARD_ACTION_PREFIX + reason,
        None,
        str(user.get("email") or "user"),
    )
    await _record_discard(
        conn,
        user=user,
        item_id=proposal.item_id,
        maker=maker,
        decision_id=proposal.decision_id,
        intent_id=intent_id,
        reason=reason,
        keys=keys,
        ip=ip,
        user_agent=user_agent,
    )
    return Outcome("done", proposal.decision_id)


async def _record_discard(
    conn: Any,
    *,
    user: Mapping[str, Any],
    item_id: str,
    maker: CouncilMaker,
    decision_id: int | None,
    intent_id: str | None,
    reason: str,
    keys: ReplayKeys,
    ip: str | None,
    user_agent: str | None,
) -> None:
    checker_id = actor_id(user)
    actors = {
        "decision_id": decision_id,
        "maker": maker.label,
        "maker_user_id": maker.user_id,
        "checker_user_id": checker_id,
    }
    await control_room_service._record_item_event(
        conn,
        user=dict(user),
        item={"id": item_id},
        event_type=DISCARDED_EVENT,
        metadata={**actors, **keys.metadata, "reason": reason},
        critical=True,
    )
    await audit_service.record_event(
        connection=conn,
        user_id=checker_id,
        email=user.get("email"),
        action=DISCARD_AUDIT,
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={**actors, "intent_id": intent_id, "reason": reason},
        critical=True,
    )


async def discard_council_proposal(
    user: Mapping[str, Any],
    proposal_id: str,
    *,
    reason: str,
    idempotency_key: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> CouncilDiscardResponse:
    require_approve(user)
    checker_id = actor_id(user)
    tenant_id, workspace_id = authority_scope(user)
    keys = replay_keys(
        workspace_id=workspace_id,
        actor=checker_id,
        operation="discard",
        key=idempotency_key,
        proposal_id=proposal_id,
    )
    pool = await auth.pool()

    async def _person(conn: Any, scoped_tenant: str | None, scoped: str) -> Outcome:
        _scope_guard(tenant_id, workspace_id, scoped_tenant, scoped)
        replayed = await _replayed(
            conn,
            workspace_id=workspace_id,
            actor=checker_id,
            keys=keys,
            event_type=DISCARDED_EVENT,
        )
        if replayed is not None:
            return replayed
        proposals = await read_person_proposals(
            conn,
            user=user,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            statuses=PENDING_STATUSES,
        )
        match = _match_person(proposals, proposal_id)
        if match is None:
            return Outcome("missing")
        return await _discard_person(
            conn,
            user=user,
            proposal=match,
            reason=reason,
            keys=keys,
            ip=ip,
            user_agent=user_agent,
        )

    outcome = await run_with_db_scope(pool, dict(user), _person)
    if outcome.kind == "missing":
        snapshot = await collect_surface_snapshot(user)
        candidates = system_candidate_items(snapshot)

        async def _system(conn: Any, scoped_tenant: str | None, scoped: str) -> Outcome:
            _scope_guard(tenant_id, workspace_id, scoped_tenant, scoped)
            replayed = await _replayed(
                conn,
                workspace_id=workspace_id,
                actor=checker_id,
                keys=keys,
                event_type=DISCARDED_EVENT,
            )
            if replayed is not None:
                return replayed
            located = await _locked_suggestion(
                conn, user=user, candidates=candidates, proposal_id=proposal_id
            )
            if located is None:
                return Outcome("missing")
            suggestion, row = located
            maker = CouncilMaker.system()
            require_council_distinct_actors(maker, checker_id)
            await _require_current(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor=checker_id,
                permissions=("control_room.approve",),
            )
            await _discard_transition(
                conn,
                user=user,
                item_id=suggestion.item_id,
                owner_user_id=row.get("owner_user_id"),
                reason=reason,
                workspace_id=workspace_id,
            )
            await _record_discard(
                conn,
                user=user,
                item_id=suggestion.item_id,
                maker=maker,
                decision_id=None,
                intent_id=None,
                reason=reason,
                keys=keys,
                ip=ip,
                user_agent=user_agent,
            )
            return Outcome("done", None)

        outcome = await run_with_db_scope(pool, dict(user), _system)
    if outcome.kind != "done":
        raise _not_found()
    return CouncilDiscardResponse(decision_id=outcome.decision_id)


async def renew_council_proposal(
    user: Mapping[str, Any],
    proposal_id: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> CouncilRenewResponse:
    require_write(user)
    maker_id = actor_id(user)
    tenant_id, workspace_id = authority_scope(user)
    pool = await auth.pool()

    async def _find(conn: Any, scoped_tenant: str | None, scoped: str) -> PersonProposal:
        _scope_guard(tenant_id, workspace_id, scoped_tenant, scoped)
        proposals = await read_person_proposals(
            conn,
            user=user,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            statuses=PENDING_STATUSES,
        )
        match = _match_person(proposals, proposal_id)
        if match is None or match.maker_user_id != maker_id:
            raise _not_found()
        return match

    proposal = await run_with_db_scope(pool, dict(user), _find)
    intent_id = await prepare_followup_intent(user, proposal.item_id)
    if intent_id is None:
        raise HTTPException(
            409,
            {
                "code": "proposal_not_renewable",
                "message": "council proposal cannot be renewed",
            },
        )
    await audit_service.record_event(
        user_id=maker_id,
        email=user.get("email"),
        action=RENEW_AUDIT,
        resource_type="control_room_item",
        resource_id=proposal.item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"decision_id": proposal.decision_id, "intent_id": intent_id},
        critical=True,
    )
    return CouncilRenewResponse(decision_id=proposal.decision_id)


__all__ = (
    "DISCARD_AUDIT",
    "RENEW_AUDIT",
    "ReplayKeys",
    "approve_council_proposal",
    "discard_council_proposal",
    "renew_council_proposal",
    "replay_keys",
)
