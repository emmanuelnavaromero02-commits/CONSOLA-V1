from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from fastapi import HTTPException

from app.schemas.control_room_council import (
    CHECKER_INFLUENCED_REASON,
    COUNCIL_SCHEMA_VERSION,
    EXPIRED_REASON,
    FOLLOWUPS_DISABLED_REASON,
    NO_AUTHOR_REASON,
    MAX_COUNCIL_EVIDENCE,
    MAX_COUNCIL_PROPOSALS,
    NEEDS_OTHER_APPROVER_REASON,
    NO_FOLLOWUP_REASON,
    SOURCE_CHANGED_REASON,
    ActionCouncilResponse,
    CouncilEvidence,
    CouncilProposal,
)
from app.services import auth
from app.services.control_room.business_access import (
    actor_id as optional_actor_id,
)
from app.services.control_room.business_action_authority import (
    action_item_is_stale,
    action_source_binding_complete,
)
from app.services.control_room.business_action_authority_evidence import (
    metadata,
    persisted_item,
)
from app.services.control_room.business_action_authority_policy import (
    EXECUTABLE_TEMPLATE_ID,
)
from app.services.control_room.business_action_authority_repository import (
    fetch_direct_rows,
)
from app.services.control_room.business_action_catalog import (
    ENABLED_ACTION_TEMPLATE_IDS_SQL,
    matches_runtime_registry,
)
from app.services.control_room.business_action_direct_contract import (
    MONITOR_ITEM_KINDS,
    direct_evidence_digest,
)
from app.services.control_room.business_action_followup_effect import COUNCIL_ORIGIN
from app.services.control_room.business_cartridge_scope import (
    business_cartridge_allowed,
)
from app.services.control_room.business_council_actors import (
    SYSTEM_MAKER,
    council_reads_workspace,
    is_council_checker,
)
from app.services.control_room.business_council_impact import council_impact
from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_evidence import has_evidence
from app.services.control_room.business_experience import (
    observation_moment,
    project_experience_fact,
)
from app.services.control_room.business_experience_copy import (
    MAX_STRUCTURAL_IDENTITY_LENGTH,
    structural_identity_is_safe,
    visible_business_copy,
)
from app.services.control_room.business_experience_narrative import (
    project_experience_narrative,
)
from app.services.control_room.business_fingerprint import (
    business_observation_fingerprint,
)
from app.services.control_room.business_projection import filter_business_items
from app.services.control_room.business_surface_identity import (
    resolve_bounded_business_surface_identity,
    surface_section_title,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
)
from app.services.control_room.business_workflow_quarantine import (
    workflow_columns_unlinked,
    workflow_is_quarantined,
)
from app.services.control_room.surface_snapshot import (
    SurfaceSnapshot,
    collect_surface_snapshot,
)
from app.services.db_scope import run_with_db_scope
from app.services.permissions import has_permission
from app.services.security_context import sign_server_payload


COUNCIL_HANDLE_PURPOSE = "control-room-council-proposal/v1"
READ_ONLY_TRANSACTION_SQL = "SET TRANSACTION READ ONLY"
MAX_PERSON_ROWS = 100
MAX_COMPLETED = 10
PENDING_STATUSES = ("decision_created",)
LISTED_STATUSES = ("decision_created", "approved")
_OPEN_STATUSES = frozenset({"open", "in_review"})
_SEVERITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_STATE_ORDER = {
    "pending_approval": 0,
    "needs_other_approver": 1,
    "source_changed": 2,
    "expired": 3,
    "no_followup": 4,
    "completed": 5,
}

PERSON_PROPOSALS_SQL = """
SELECT item.tenant_id::text AS tenant_id,
       item.workspace_id::text AS workspace_id,
       item.owner_user_id, item.item_id, item.cartridge_id, item.domain,
       item.source_dataset, item.item_kind, item.title, item.severity,
       item.status, item.decision_id, item.entity_kind, item.entity_id,
       item.entity_label, item.anomaly_type, item.metadata,
       item.first_seen_at, item.last_seen_at, item.resolved_at,
       item.dismissed_at, item.impact_estimate, item.impact_currency,
       item.confidence, item.priority_score, item.selected_option_id,
       item.execution_status,
       decision.workspace_id::text AS decision_workspace_id,
       decision.status AS decision_status,
       decision.created_at AS decision_created_at,
       decision.commitment_date AS decision_commitment_date,
       decision.created_by_id AS decision_created_by_id,
       decision.created_by AS decision_created_by
  FROM control_room_items AS item
  JOIN decisions AS decision
    ON decision.id = item.decision_id
   AND decision.workspace_id = item.workspace_id
 WHERE item.tenant_id = $1::uuid
   AND item.workspace_id = $2::uuid
   AND decision.status = 'open'
   AND item.status = ANY($3::text[])
   AND (
       $4::bigint IS NULL
       OR item.owner_user_id = $4::bigint
       OR decision.created_by_id = $4::bigint
   )
 ORDER BY decision.created_at DESC, decision.id DESC
 LIMIT $5
"""
LATEST_INTENTS_SQL = """
SELECT intent.id::text AS id, intent.item_id, intent.maker_user_id,
       intent.checker_user_id, intent.state, intent.expires_at,
       intent.created_at, intent.observation_fingerprint
  FROM control_room_action_intents AS intent
 WHERE intent.tenant_id = $1::uuid
   AND intent.workspace_id = $2::uuid
   AND intent.item_id = ANY($3::text[])
   AND intent.template_id = 'create_followup_task'
   AND intent.created_at = (
       SELECT max(latest.created_at)
         FROM control_room_action_intents AS latest
        WHERE latest.tenant_id = intent.tenant_id
          AND latest.workspace_id = intent.workspace_id
          AND latest.item_id = intent.item_id
          AND latest.maker_user_id = intent.maker_user_id
          AND latest.template_id = 'create_followup_task'
   )
 ORDER BY intent.item_id, intent.maker_user_id, intent.id DESC
"""
WORKSPACE_THRESHOLDS_SQL = """
SELECT id::text AS id, cartridge_id, anomaly_type, metric
  FROM control_room_thresholds
 WHERE workspace_id = $1::uuid
"""
THRESHOLD_AUTHORSHIP_SQL = """
SELECT resource_id
  FROM audit_events
 WHERE action = 'control_room.threshold.upsert'
   AND user_id = $1
   AND resource_id = ANY($2::text[])
"""


@dataclass(frozen=True)
class PersonProposal:
    row: Mapping[str, Any]
    item_id: str
    decision_id: int
    maker_user_id: int | None
    intent: Mapping[str, Any] | None
    handle: str

    @property
    def system_made(self) -> bool:
        return str(self.row.get("decision_created_by") or "") == SYSTEM_MAKER


@dataclass(frozen=True)
class SystemSuggestion:
    live: Mapping[str, Any]
    row: Mapping[str, Any]
    item_id: str
    fingerprint: str
    evidence_digest: str
    handle: str


def _scope(user: Mapping[str, Any]) -> tuple[str, str]:
    tenant = str(user.get("active_tenant_id") or user.get("tenant_id") or "").strip()
    workspace = str(
        user.get("active_workspace_id") or user.get("workspace_id") or ""
    ).strip()
    if not tenant or not workspace:
        raise HTTPException(404, "workspace scope not found")
    return tenant, workspace


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    parsed = value if value.tzinfo else value.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _status(value: object) -> str:
    return str(value or "open").strip().lower() or "open"


def proposal_handle(
    *,
    origin: str,
    tenant_id: str,
    workspace_id: str,
    viewer_id: int | None,
    item_id: str,
    decision_id: int | None = None,
    intent_id: str | None = None,
    fingerprint: str | None = None,
    evidence_digest: str | None = None,
) -> str:
    payload = json.dumps(
        {
            "origin": origin,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "viewer_id": viewer_id,
            "item_id": item_id,
            "decision_id": decision_id,
            "intent_id": intent_id or "",
            "fingerprint": fingerprint or "",
            "evidence_digest": evidence_digest or "",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sign_server_payload(payload, purpose=COUNCIL_HANDLE_PURPOSE)


def person_owner_filter(user: Mapping[str, Any]) -> int | None:
    if council_reads_workspace(user):
        return None
    return optional_actor_id(user.get("id")) or 0


def _latest_by_maker(
    intents: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, int], Mapping[str, Any]]:
    latest: dict[tuple[str, int], Mapping[str, Any]] = {}
    for intent in intents:
        maker = optional_actor_id(intent.get("maker_user_id"))
        if maker is None:
            continue
        key = (str(intent.get("item_id") or ""), maker)
        current = latest.get(key)
        created = _utc(intent.get("created_at"))
        if current is None or (
            created is not None
            and (_utc(current.get("created_at")) or created) < created
        ):
            latest[key] = intent
    return latest


async def read_person_proposals(
    conn: Any,
    *,
    user: Mapping[str, Any],
    tenant_id: str,
    workspace_id: str,
    statuses: Sequence[str] = LISTED_STATUSES,
) -> list[PersonProposal]:
    rows = await conn.fetch(
        PERSON_PROPOSALS_SQL,
        tenant_id,
        workspace_id,
        list(statuses),
        person_owner_filter(user),
        MAX_PERSON_ROWS,
    )
    rows = [
        dict(row)
        for row in rows
        if business_cartridge_allowed(
            user, str(row.get("cartridge_id") or ""), allow_platform=True
        )
    ]
    item_ids = sorted(
        {str(row.get("item_id") or "") for row in rows if row.get("item_id")}
    )
    intents = (
        await conn.fetch(LATEST_INTENTS_SQL, tenant_id, workspace_id, item_ids)
        if item_ids
        else []
    )
    latest = _latest_by_maker([dict(intent) for intent in intents])
    viewer = optional_actor_id(user.get("id"))
    proposals: list[PersonProposal] = []
    for row in rows:
        item_id = str(row.get("item_id") or "")
        decision_id = optional_actor_id(row.get("decision_id"))
        if (
            not item_id
            or decision_id is None
            or str(row.get("decision_status") or "") != "open"
            or str(row.get("decision_workspace_id") or "") != workspace_id
            or str(row.get("status") or "") not in statuses
        ):
            continue
        maker = optional_actor_id(row.get("decision_created_by_id"))
        intent = latest.get((item_id, maker)) if maker is not None else None
        proposals.append(
            PersonProposal(
                row=row,
                item_id=item_id,
                decision_id=decision_id,
                maker_user_id=maker,
                intent=intent,
                handle=proposal_handle(
                    origin="person",
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    viewer_id=viewer,
                    item_id=item_id,
                    decision_id=decision_id,
                    intent_id=str(intent.get("id")) if intent else None,
                ),
            )
        )
    return proposals


def _live_system_candidate(live: Mapping[str, Any]) -> bool:
    try:
        kind = str(live.get("kind") or live.get("item_kind") or "").strip().lower()
        execution = str(live.get("execution_status") or "not_started").strip()
        return bool(
            str(live.get("id") or live.get("item_id") or "").strip()
            and kind not in MONITOR_ITEM_KINDS
            and _status(live.get("status")) in _OPEN_STATUSES
            and live.get("decision_id") in (None, 0)
            and execution == "not_started"
            and not action_item_is_stale(live)
            and action_source_binding_complete(live)
            and classify_business_item(live).eligible
            and has_evidence(live)
        )
    except (TypeError, ValueError):
        return False


def system_suggestion_match(
    live: Mapping[str, Any],
    row: Mapping[str, Any] | None,
    *,
    tenant_id: str,
    workspace_id: str,
) -> tuple[str, str] | None:
    """Returns (fingerprint, evidence digest) when the stored row equals the live finding."""
    if row is None or not _live_system_candidate(live):
        return None
    try:
        item_id = str(live.get("id") or live.get("item_id") or "").strip()
        if (
            str(row.get("item_id") or "") != item_id
            or str(row.get("tenant_id") or "") != tenant_id
            or str(row.get("workspace_id") or "") != workspace_id
            or workflow_is_quarantined(row)
        ):
            return None
        persisted_status = _status(row.get("status"))
        if (
            persisted_status not in _OPEN_STATUSES
            or persisted_status != _status(live.get("status"))
            or row.get("decision_id") is not None
            or not workflow_columns_unlinked(row)
            or DECISION_PROVENANCE_KEY in metadata(row)
        ):
            return None
        persisted = persisted_item(row, item_id)
        if persisted is None or not classify_business_item(persisted).eligible:
            return None
        fingerprint = business_observation_fingerprint(live)
        stored = str(metadata(row).get(CURRENT_ELIGIBILITY_FINGERPRINT_KEY) or "")
        if not fingerprint or not (
            fingerprint == business_observation_fingerprint(persisted) == stored
        ):
            return None
        evidence = direct_evidence_digest(live)
        if evidence is None or evidence != direct_evidence_digest(persisted):
            return None
        return fingerprint, evidence
    except (TypeError, ValueError):
        return None


def system_suggestions(
    live_items: Sequence[Mapping[str, Any]],
    rows: Mapping[str, Mapping[str, Any]],
    *,
    user: Mapping[str, Any],
    tenant_id: str,
    workspace_id: str,
) -> list[SystemSuggestion]:
    viewer = optional_actor_id(user.get("id"))
    suggestions: list[SystemSuggestion] = []
    for live in live_items:
        item_id = str(live.get("id") or live.get("item_id") or "").strip()
        matched = system_suggestion_match(
            live, rows.get(item_id), tenant_id=tenant_id, workspace_id=workspace_id
        )
        if matched is None:
            continue
        fingerprint, evidence = matched
        suggestions.append(
            SystemSuggestion(
                live=live,
                row=rows[item_id],
                item_id=item_id,
                fingerprint=fingerprint,
                evidence_digest=evidence,
                handle=proposal_handle(
                    origin="system",
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    viewer_id=viewer,
                    item_id=item_id,
                    fingerprint=fingerprint,
                    evidence_digest=evidence,
                ),
            )
        )
    return suggestions


def system_candidate_items(snapshot: SurfaceSnapshot) -> list[Mapping[str, Any]]:
    return [
        item
        for item in filter_business_items(snapshot.items)
        if _live_system_candidate(item)
    ]


def person_state(
    proposal: PersonProposal, *, now: datetime, viewer_id: int | None
) -> str:
    row = proposal.row
    if str(row.get("status") or "") == "approved":
        return "completed"
    intent = proposal.intent
    if intent is None:
        return "no_followup"
    state = str(intent.get("state") or "")
    if state == "pending_approval":
        expires_at = _utc(intent.get("expires_at"))
        if expires_at is None or expires_at <= now:
            return "expired"
        stored = str(metadata(row).get(CURRENT_ELIGIBILITY_FINGERPRINT_KEY) or "")
        if not stored or stored != str(intent.get("observation_fingerprint") or ""):
            return "source_changed"
        if viewer_id is not None and viewer_id == proposal.maker_user_id:
            return "needs_other_approver"
        return "pending_approval"
    if state in {"stale", "rejected"}:
        return "source_changed"
    return "no_followup"


def threshold_keys(item: Mapping[str, Any]) -> set[tuple[str, str, str]]:
    details = item.get("details") if isinstance(item.get("details"), Mapping) else {}
    keys: set[tuple[str, str, str]] = set()
    for source in (item.get("thresholds_applied"), details.get("thresholds")):
        for entry in source if isinstance(source, Sequence) else ():
            if not isinstance(entry, Mapping) or entry.get("source") != "workspace":
                continue
            cartridge, anomaly, metric = (
                str(entry.get(field) or "").strip()
                for field in ("cartridge_id", "anomaly_type", "metric")
            )
            if cartridge and anomaly and metric:
                keys.add((cartridge, anomaly, metric))
    return keys


async def authored_threshold_keys(
    conn: Any, *, workspace_id: str, user_id: int | None
) -> set[tuple[str, str, str]]:
    if user_id is None:
        return set()
    thresholds = {
        str(row["id"]): (
            str(row.get("cartridge_id") or ""),
            str(row.get("anomaly_type") or ""),
            str(row.get("metric") or ""),
        )
        for row in await conn.fetch(WORKSPACE_THRESHOLDS_SQL, workspace_id)
    }
    if not thresholds:
        return set()
    authored = await conn.fetch(THRESHOLD_AUTHORSHIP_SQL, user_id, sorted(thresholds))
    return {
        thresholds[str(row["resource_id"])]
        for row in authored
        if str(row.get("resource_id") or "") in thresholds
    }


def checker_influenced(
    item: Mapping[str, Any], authored: set[tuple[str, str, str]]
) -> bool:
    return bool(threshold_keys(item) & authored)


async def followups_enabled(conn: Any) -> bool:
    rows = await conn.fetch(ENABLED_ACTION_TEMPLATE_IDS_SQL, [EXECUTABLE_TEMPLATE_ID], 1)
    return any(
        str(row.get("template_id") or "") == EXECUTABLE_TEMPLATE_ID
        and matches_runtime_registry(row)
        for row in rows
    )


def _completed_by_council(row: Mapping[str, Any]) -> bool:
    writeback = metadata(row).get("writeback_result")
    return (
        str(row.get("execution_status") or "") == "executed"
        and isinstance(writeback, Mapping)
        and writeback.get("origin") == COUNCIL_ORIGIN
    )


def _evidence(
    item: Mapping[str, Any],
    *,
    entity_label: str | None,
    metric_text: str | None,
    observed_at: datetime | None,
) -> list[CouncilEvidence]:
    evidence: list[CouncilEvidence] = []
    if entity_label:
        evidence.append(CouncilEvidence(label="Entidad", value=entity_label))
    if metric_text:
        evidence.append(CouncilEvidence(label="Indicador", value=metric_text))
    if observed_at is not None:
        evidence.append(
            CouncilEvidence(label="Fecha del dato", value=observed_at.date().isoformat())
        )
    return evidence[:MAX_COUNCIL_EVIDENCE]


def _metric_text(metric: Any) -> str | None:
    if metric is None:
        return None
    rendered = f"{float(metric.value):,.2f}".rstrip("0").rstrip(".")
    unit = f" {metric.unit}" if metric.unit else ""
    return f"{metric.name}: {rendered}{unit}"[:240]


def _display(
    item: Mapping[str, Any], narrative: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    identity = resolve_bounded_business_surface_identity(
        item, max_length=MAX_STRUCTURAL_IDENTITY_LENGTH
    )
    if identity is None or not structural_identity_is_safe(item, identity):
        return None
    fact = project_experience_fact(item, identity)
    title = (
        fact.title
        if fact is not None
        else visible_business_copy(item, identity, item.get("title"), max_length=240)
    )
    if not title:
        return None
    severity = str(item.get("severity") or "medium").strip().lower()
    entity_label = (
        fact.entity_label
        if fact is not None
        else visible_business_copy(
            item, identity, item.get("entity_label"), max_length=240
        )
    )
    metric = fact.metric if fact is not None else None
    observed_at = fact.observed_at if fact is not None else observation_moment(item)
    section = surface_section_title(
        item,
        identity,
        visible_copy=lambda value: visible_business_copy(
            item, identity, value, max_length=240
        ),
    )
    return {
        "title": title,
        "section_title": section or None,
        "severity": severity if severity in _SEVERITY_WEIGHT else "medium",
        "observed_at": observed_at,
        "impact": council_impact(item, metric),
        "evidence": _evidence(
            item,
            entity_label=entity_label,
            metric_text=_metric_text(metric),
            observed_at=observed_at,
        ),
        "narrative": project_experience_narrative(narrative, item, identity),
    }


def _commitment(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def project_person_proposal(
    proposal: PersonProposal,
    *,
    user: Mapping[str, Any],
    live: Mapping[str, Any] | None,
    narrative: Mapping[str, Any] | None,
    now: datetime,
    enabled: bool = True,
) -> CouncilProposal | None:
    persisted = persisted_item(proposal.row, proposal.item_id)
    source = live if live is not None else persisted
    if source is None:
        return None
    display = _display(source, narrative if live is not None else None)
    if display is None:
        return None
    viewer = optional_actor_id(user.get("id"))
    state = person_state(proposal, now=now, viewer_id=viewer)
    system_made = proposal.system_made
    authorless = proposal.maker_user_id is None and not system_made
    authored = not system_made and viewer is not None and viewer == proposal.maker_user_id
    approver = has_permission(dict(user), "control_room.approve")
    can_approve = (
        state == "pending_approval"
        and enabled
        and is_council_checker(user)
        and not authored
    )
    reason: str | None = None
    if state == "completed":
        reason = None
    elif authorless:
        reason = NO_AUTHOR_REASON
    elif state in {"pending_approval", "needs_other_approver"} and not enabled:
        reason = FOLLOWUPS_DISABLED_REASON
    elif state in {"pending_approval", "needs_other_approver"} and not can_approve:
        reason = NEEDS_OTHER_APPROVER_REASON
    elif state == "expired":
        reason = EXPIRED_REASON
    elif state == "no_followup":
        reason = NO_FOLLOWUP_REASON
    elif state == "source_changed":
        reason = SOURCE_CHANGED_REASON
    renewable = (
        state in {"expired", "no_followup", "source_changed"}
        and not authorless
        and enabled
    )
    return CouncilProposal(
        proposal_id=proposal.handle,
        origin="system" if system_made else "person",
        authored_by_you=authored,
        decision_id=proposal.decision_id,
        created_at=_utc(proposal.row.get("decision_created_at")),
        commitment_date=_commitment(proposal.row.get("decision_commitment_date")),
        state=state,
        can_approve=can_approve,
        can_discard=state != "completed" and not authored and not authorless and approver,
        can_renew=renewable
        and authored
        and has_permission(dict(user), "control_room.write"),
        disabled_reason=reason,
        **display,
    )


def project_system_suggestion(
    suggestion: SystemSuggestion,
    *,
    user: Mapping[str, Any],
    narrative: Mapping[str, Any] | None,
    influenced: bool = False,
) -> CouncilProposal | None:
    display = _display(suggestion.live, narrative)
    if display is None:
        return None
    can_approve = is_council_checker(user) and not influenced
    return CouncilProposal(
        proposal_id=suggestion.handle,
        origin="system",
        authored_by_you=False,
        decision_id=None,
        created_at=_utc(suggestion.row.get("first_seen_at")),
        commitment_date=None,
        state="needs_other_approver" if influenced else "pending_approval",
        can_approve=can_approve,
        can_discard=has_permission(dict(user), "control_room.approve"),
        can_renew=False,
        disabled_reason=(
            None
            if can_approve
            else CHECKER_INFLUENCED_REASON
            if influenced
            else NEEDS_OTHER_APPROVER_REASON
        ),
        **display,
    )


def _person_sort_key(proposal: CouncilProposal) -> tuple[int, float]:
    created = proposal.created_at.timestamp() if proposal.created_at else 0.0
    return (_STATE_ORDER.get(proposal.state, 9), -created)


def _system_sort_key(proposal: CouncilProposal) -> tuple[int, float, str]:
    observed = proposal.observed_at.timestamp() if proposal.observed_at else 0.0
    return (-_SEVERITY_WEIGHT[proposal.severity], -observed, proposal.title)


@dataclass(frozen=True)
class CouncilRows:
    persons: list[PersonProposal]
    rows: dict[str, dict[str, Any]]
    enabled: bool
    authored_thresholds: set[tuple[str, str, str]]


async def read_council_rows(
    user: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> CouncilRows:
    tenant_id, workspace_id = _scope(user)
    candidate_ids = [
        str(item.get("id") or item.get("item_id") or "") for item in candidates
    ]
    check_thresholds = is_council_checker(user) and any(
        threshold_keys(item) for item in candidates
    )
    pool = await auth.pool()

    async def _read(
        conn: Any, scoped_tenant: str | None, scoped_workspace: str
    ) -> CouncilRows:
        await conn.execute(READ_ONLY_TRANSACTION_SQL)
        if str(scoped_tenant or "") != tenant_id or scoped_workspace != workspace_id:
            raise HTTPException(404, "workspace scope not found")
        persons = await read_person_proposals(
            conn, user=user, tenant_id=tenant_id, workspace_id=workspace_id
        )
        rows = (
            await fetch_direct_rows(
                conn,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                item_ids=candidate_ids,
            )
            if candidate_ids
            else {}
        )
        authored = (
            await authored_threshold_keys(
                conn,
                workspace_id=workspace_id,
                user_id=optional_actor_id(user.get("id")),
            )
            if check_thresholds
            else set()
        )
        return CouncilRows(persons, rows, await followups_enabled(conn), authored)

    return await run_with_db_scope(pool, dict(user), _read)


async def build_action_council(user: Mapping[str, Any]) -> ActionCouncilResponse:
    tenant_id, workspace_id = _scope(user)
    snapshot = await collect_surface_snapshot(user)
    live_by_id = {
        str(item.get("id") or item.get("item_id") or ""): item
        for item in filter_business_items(snapshot.items)
    }
    candidates = system_candidate_items(snapshot)
    read = await read_council_rows(user, candidates)
    now = datetime.now(UTC)
    person_proposals: list[CouncilProposal] = []
    completed = 0
    listed_items: set[str] = set()
    for proposal in read.persons:
        if str(proposal.row.get("status") or "") == "approved":
            if not _completed_by_council(proposal.row) or completed >= MAX_COMPLETED:
                continue
            completed += 1
        elif proposal.system_made:
            continue
        projected = project_person_proposal(
            proposal,
            user=user,
            live=live_by_id.get(proposal.item_id),
            narrative=snapshot.narratives.get(proposal.item_id),
            now=now,
            enabled=read.enabled,
        )
        if projected is not None:
            listed_items.add(proposal.item_id)
            person_proposals.append(projected)
    person_proposals.sort(key=_person_sort_key)
    system_proposals = (
        [
            projected
            for suggestion in system_suggestions(
                candidates,
                read.rows,
                user=user,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            if suggestion.item_id not in listed_items
            and (
                projected := project_system_suggestion(
                    suggestion,
                    user=user,
                    narrative=snapshot.narratives.get(suggestion.item_id),
                    influenced=checker_influenced(
                        suggestion.live, read.authored_thresholds
                    ),
                )
            )
            is not None
        ]
        if read.enabled
        else []
    )
    system_proposals.sort(key=_system_sort_key)
    proposals = [*person_proposals, *system_proposals][:MAX_COUNCIL_PROPOSALS]
    return ActionCouncilResponse(
        schema_version=COUNCIL_SCHEMA_VERSION,
        generated_at=snapshot.generated_at,
        proposals=proposals,
    )


__all__ = (
    "COUNCIL_HANDLE_PURPOSE",
    "LATEST_INTENTS_SQL",
    "PERSON_PROPOSALS_SQL",
    "PENDING_STATUSES",
    "THRESHOLD_AUTHORSHIP_SQL",
    "CouncilRows",
    "PersonProposal",
    "SystemSuggestion",
    "authored_threshold_keys",
    "build_action_council",
    "checker_influenced",
    "followups_enabled",
    "person_owner_filter",
    "person_state",
    "project_person_proposal",
    "project_system_suggestion",
    "proposal_handle",
    "read_person_proposals",
    "system_candidate_items",
    "system_suggestion_match",
    "system_suggestions",
    "threshold_keys",
)
