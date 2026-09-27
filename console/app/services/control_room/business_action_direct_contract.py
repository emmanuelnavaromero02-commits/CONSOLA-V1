from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.services.control_room.business_access import actor_id as optional_actor_id
from app.services.control_room.business_action_authority import (
    action_item_is_stale,
    action_source_binding_complete,
)
from app.services.control_room.business_action_authority_evidence import (
    metadata,
    persisted_item,
)
from app.services.control_room.business_action_authority_policy import (
    APPROVE_EXCEPTION_TEMPLATE_ID,
    DECISION_PROPOSAL_TEMPLATE_ID,
    DIRECT_ACTION_TEMPLATE_IDS,
    OPEN_IN_STUDIO_TEMPLATE_ID,
    REOPEN_EXCEPTION_TEMPLATE_ID,
    decision_contract_digest,
)
from app.services.control_room.business_action_authorization_snapshot import (
    AuthorizationSnapshot,
)
from app.services.control_room.business_action_digest import action_contract_digest
from app.services.control_room.business_cartridge_scope import (
    business_cartridge_allowed,
)
from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_evidence import has_evidence
from app.services.control_room.business_fingerprint import (
    business_observation_fingerprint,
)
from app.services.control_room.business_template_contract import (
    template_contract_digest,
)
from app.services.control_room.business_runtime_evidence import (
    canonical_runtime_row_reference,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
    DECISION_PROVENANCE_KEY,
)
from app.services.control_room.business_workflow_quarantine import (
    workflow_columns_unlinked,
    workflow_is_quarantined,
    workflow_reopen_allowed,
)
from app.services.permission_roles import PLATFORM_ADMIN_ROLES
from app.services.permissions import has_permission, user_role


DIRECT_CONTRACT_VERSION = "control-room-direct-action/v1"
NO_DECISION_DIGEST = action_contract_digest(
    {"version": DIRECT_CONTRACT_VERSION, "decision": "none"}
)
EXCEPTION_RESOLUTION = "exception_approved"
RESOLUTION_KEYS = (
    "resolution",
    "resolution_actor_id",
    "resolution_reason",
    "resolution_at",
    "resolution_observation_fingerprint",
    "resolution_evidence_digest",
)
STUDIO_TAB = "capas"
MONITOR_ITEM_KINDS = frozenset({"agent_alert"})
LIVE_BOUND_TEMPLATE_IDS = frozenset(
    {
        APPROVE_EXCEPTION_TEMPLATE_ID,
        DECISION_PROPOSAL_TEMPLATE_ID,
        OPEN_IN_STUDIO_TEMPLATE_ID,
    }
)
_OPEN_STATUSES = frozenset({"open", "in_review"})
_STUDIO_STATUSES = frozenset({"open", "in_review", "decision_created"})
_CARTRIDGE_ID = re.compile(r"^[a-z0-9_]{1,120}$")
_HEX_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_TARGETS: dict[str, dict[str, str]] = {
    APPROVE_EXCEPTION_TEMPLATE_ID: {
        "system": "omega_control_room",
        "adapter": "exception_approval",
        "resource": "control_room_items",
    },
    DECISION_PROPOSAL_TEMPLATE_ID: {
        "system": "omega_control_room",
        "adapter": "decision_proposal",
        "resource": "decisions",
    },
    REOPEN_EXCEPTION_TEMPLATE_ID: {
        "system": "omega_control_room",
        "adapter": "exception_reopen",
        "resource": "control_room_items",
    },
}


class DirectMatch(str, Enum):
    MATCH = "match"
    NEEDS_REFRESH = "needs_refresh"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True, repr=False)
class DirectActionContract:
    item: Mapping[str, Any]
    item_id: str
    template_id: str
    maker_user_id: int
    tenant_id: str
    workspace_id: str
    cartridge_id: str
    decision_id: int | None
    binding_digest: str
    evidence_digest: str
    observation_fingerprint: str
    template_contract_digest: str
    target_digest: str
    decision_digest: str
    contract_digest: str
    access_revision_digest: str
    rbac_policy_digest: str


def direct_evidence_digest(item: Mapping[str, Any]) -> str | None:
    references: dict[str, dict[str, Any]] = {}
    for source in (item, metadata(item)):
        raw = source.get("evidence_refs")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
            continue
        for value in raw:
            if not isinstance(value, Mapping):
                continue
            canonical = canonical_runtime_row_reference(value)
            if canonical is not None:
                references[action_contract_digest(canonical)] = canonical
    if not references:
        return None
    return action_contract_digest(
        {
            "version": DIRECT_CONTRACT_VERSION,
            "runtime_references": [references[key] for key in sorted(references)],
        }
    )


def _status(value: object) -> str:
    return str(value or "open").strip().lower() or "open"


def user_can_view_studio(user: Mapping[str, Any]) -> bool:
    return user_role(dict(user)) in PLATFORM_ADMIN_ROLES and has_permission(
        dict(user), "studio.read"
    )


def studio_href(cartridge_id: str) -> str:
    if not _CARTRIDGE_ID.fullmatch(cartridge_id):
        raise ValueError("studio cartridge is invalid")
    return f"/studio?cartridge={cartridge_id}&tab={STUDIO_TAB}"


def _decision_id(row: Mapping[str, Any]) -> int | None:
    value = row.get("decision_id")
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("decision id is invalid")
    resolved = int(value)
    if resolved <= 0:
        raise ValueError("decision id is invalid")
    return resolved


def _live_decision_id(live: Mapping[str, Any]) -> int | None:
    value = live.get("decision_id")
    if value in (None, 0):
        return None
    if isinstance(value, bool):
        raise ValueError("decision id is invalid")
    return int(value)


def _item_kind(item: Mapping[str, Any]) -> str:
    return str(item.get("kind") or item.get("item_kind") or "").strip().lower()


def _live_cartridge(live: Mapping[str, Any]) -> str:
    return str(live.get("cartridge") or live.get("cartridge_id") or "").strip()


def _scope_matches(
    values: Sequence[object], authorization: AuthorizationSnapshot
) -> bool:
    tenant, workspace = values
    return (
        str(tenant or "") == authorization.tenant_id
        and str(workspace or "") == authorization.workspace_id
    )


def _actor_matches(
    user: Mapping[str, Any], authorization: AuthorizationSnapshot
) -> bool:
    return (
        authorization.permission == "control_room.write"
        and optional_actor_id(user.get("id")) == authorization.actor_user_id
    )


def _owner_allowed(
    row: Mapping[str, Any],
    authorization: AuthorizationSnapshot,
    *,
    live_owner: object = None,
) -> bool:
    persisted_owner = row.get("owner_user_id")
    if live_owner is not None and str(persisted_owner or "") != str(live_owner):
        return False
    return authorization.workspace_wide or str(persisted_owner or "") == str(
        authorization.actor_user_id
    )


def live_direct_candidate(
    live: Mapping[str, Any],
    template_id: str,
    *,
    user: Mapping[str, Any],
    authorization: AuthorizationSnapshot,
) -> bool:
    """Live-side gates that a stored row can never satisfy on its behalf."""
    try:
        if template_id not in LIVE_BOUND_TEMPLATE_IDS:
            return False
        if not _actor_matches(user, authorization):
            return False
        item_id = str(live.get("id") or live.get("item_id") or "").strip()
        if not item_id or _item_kind(live) in MONITOR_ITEM_KINDS:
            return False
        if not _scope_matches(
            (live.get("tenant_id"), live.get("workspace_id")), authorization
        ):
            return False
        live_status = _status(live.get("status"))
        execution = str(live.get("execution_status") or "not_started").strip()
        if execution != "not_started":
            return False
        if template_id == OPEN_IN_STUDIO_TEMPLATE_ID:
            cartridge = _live_cartridge(live)
            if not (
                live_status in _STUDIO_STATUSES
                and _CARTRIDGE_ID.fullmatch(cartridge)
                and user_can_view_studio(user)
                and business_cartridge_allowed(user, cartridge)
            ):
                return False
        elif live_status not in _OPEN_STATUSES or _live_decision_id(live) is not None:
            return False
        return bool(
            action_source_binding_complete(live)
            and classify_business_item(live).eligible
            and has_evidence(live)
        )
    except (TypeError, ValueError):
        return False


def _template_row_allowed(
    template_id: str,
    *,
    row: Mapping[str, Any],
    persisted_status: str,
    decision_id: int | None,
) -> bool:
    row_metadata = metadata(row)
    if template_id in {APPROVE_EXCEPTION_TEMPLATE_ID, DECISION_PROPOSAL_TEMPLATE_ID}:
        return bool(
            persisted_status in _OPEN_STATUSES
            and decision_id is None
            and workflow_columns_unlinked(row)
            and DECISION_PROVENANCE_KEY not in row_metadata
        )
    if template_id == OPEN_IN_STUDIO_TEMPLATE_ID:
        return persisted_status in _STUDIO_STATUSES
    return False


def _target(template_id: str, cartridge_id: str) -> dict[str, str]:
    if template_id == OPEN_IN_STUDIO_TEMPLATE_ID:
        return {"system": "omega_studio", "cartridge": cartridge_id, "tab": STUDIO_TAB}
    return dict(_TARGETS[template_id])


def _contract(
    *,
    item: Mapping[str, Any],
    item_id: str,
    template: Mapping[str, Any],
    authorization: AuthorizationSnapshot,
    cartridge_id: str,
    decision_id: int | None,
    state: str,
    observation_fingerprint: str,
    evidence: str,
) -> DirectActionContract:
    template_id = str(template["template_id"])
    target_digest = action_contract_digest(
        {
            "version": DIRECT_CONTRACT_VERSION,
            "template_id": template_id,
            "target": _target(template_id, cartridge_id),
        }
    )
    template_digest = template_contract_digest(template)
    decision_digest = (
        decision_contract_digest(authorization.workspace_id, decision_id)
        if decision_id is not None
        else NO_DECISION_DIGEST
    )
    binding = {
        "version": DIRECT_CONTRACT_VERSION,
        "tenant_id": authorization.tenant_id,
        "workspace_id": authorization.workspace_id,
        "maker_user_id": authorization.actor_user_id,
        "item_id": item_id,
        "template_id": template_id,
        "status": state,
        "observation_fingerprint": observation_fingerprint,
        "evidence_digest": evidence,
        "template_contract_digest": template_digest,
        "target_digest": target_digest,
        "decision_digest": decision_digest,
    }
    binding_digest = action_contract_digest(binding)
    return DirectActionContract(
        item=item,
        item_id=item_id,
        template_id=template_id,
        maker_user_id=authorization.actor_user_id,
        tenant_id=authorization.tenant_id,
        workspace_id=authorization.workspace_id,
        cartridge_id=cartridge_id,
        decision_id=decision_id,
        binding_digest=binding_digest,
        evidence_digest=evidence,
        observation_fingerprint=observation_fingerprint,
        template_contract_digest=template_digest,
        target_digest=target_digest,
        decision_digest=decision_digest,
        contract_digest=action_contract_digest(
            {**binding, "binding_digest": binding_digest}
        ),
        access_revision_digest=authorization.access_revision_digest,
        rbac_policy_digest=authorization.rbac_policy_digest,
    )


def classify_direct_action(
    live: Mapping[str, Any],
    row: Mapping[str, Any] | None,
    authorization: AuthorizationSnapshot,
    template: Mapping[str, Any],
    *,
    user: Mapping[str, Any],
) -> tuple[DirectMatch, DirectActionContract | None]:
    """Bind a live observation to its stored row; drift asks for a refresh."""
    template_id = str(template.get("template_id") or "")
    if not live_direct_candidate(
        live, template_id, user=user, authorization=authorization
    ):
        return DirectMatch.INELIGIBLE, None
    if action_item_is_stale(live):
        # A refresh cannot make stale source data current; no action is offered.
        return DirectMatch.INELIGIBLE, None
    if row is None:
        return DirectMatch.NEEDS_REFRESH, None
    try:
        item_id = str(live.get("id") or live.get("item_id") or "").strip()
        if str(row.get("item_id") or "") != item_id or not _scope_matches(
            (row.get("tenant_id"), row.get("workspace_id")), authorization
        ):
            return DirectMatch.INELIGIBLE, None
        if not _owner_allowed(row, authorization, live_owner=live.get("owner_user_id")):
            return DirectMatch.INELIGIBLE, None
        if workflow_is_quarantined(row):
            return DirectMatch.INELIGIBLE, None
        decision_id = _decision_id(row)
        if decision_id is not None and (
            str(row.get("decision_workspace_id") or "") != authorization.workspace_id
            or _live_decision_id(live) != decision_id
        ):
            return DirectMatch.INELIGIBLE, None
        cartridge_id = str(row.get("cartridge_id") or "").strip()
        live_cartridge = _live_cartridge(live)
        if live_cartridge and live_cartridge != cartridge_id:
            return DirectMatch.INELIGIBLE, None
        persisted = persisted_item(row, item_id)
        if persisted is None or not classify_business_item(persisted).eligible:
            return DirectMatch.NEEDS_REFRESH, None
        persisted_status = _status(row.get("status"))
        if _status(live.get("status")) != persisted_status:
            return DirectMatch.NEEDS_REFRESH, None
        live_fingerprint = business_observation_fingerprint(live)
        stored_fingerprint = str(
            metadata(row).get(CURRENT_ELIGIBILITY_FINGERPRINT_KEY) or ""
        )
        if not live_fingerprint or not (
            live_fingerprint
            == business_observation_fingerprint(persisted)
            == stored_fingerprint
        ):
            return DirectMatch.NEEDS_REFRESH, None
        live_evidence = direct_evidence_digest(live)
        if live_evidence is None or live_evidence != direct_evidence_digest(persisted):
            return DirectMatch.NEEDS_REFRESH, None
        if not _template_row_allowed(
            template_id,
            row=row,
            persisted_status=persisted_status,
            decision_id=decision_id,
        ):
            return DirectMatch.INELIGIBLE, None
        return DirectMatch.MATCH, _contract(
            item=persisted,
            item_id=item_id,
            template=template,
            authorization=authorization,
            cartridge_id=cartridge_id,
            decision_id=decision_id,
            state=persisted_status,
            observation_fingerprint=live_fingerprint,
            evidence=live_evidence,
        )
    except (TypeError, ValueError):
        return DirectMatch.INELIGIBLE, None


def match_direct_action_item(
    live: Mapping[str, Any],
    row: Mapping[str, Any],
    authorization: AuthorizationSnapshot,
    template: Mapping[str, Any],
    *,
    user: Mapping[str, Any],
) -> DirectActionContract | None:
    status, contract = classify_direct_action(
        live, row, authorization, template, user=user
    )
    return contract if status is DirectMatch.MATCH else None


def resolution_digest(row: Mapping[str, Any]) -> str | None:
    row_metadata = metadata(row)
    if row_metadata.get("resolution") != EXCEPTION_RESOLUTION:
        return None
    approved_at = row_metadata.get("resolution_at")
    actor = optional_actor_id(row_metadata.get("resolution_actor_id"))
    if not isinstance(approved_at, str) or not approved_at.strip() or actor is None:
        return None
    return action_contract_digest(
        {
            "version": DIRECT_CONTRACT_VERSION,
            "resolution": EXCEPTION_RESOLUTION,
            "item_id": str(row.get("item_id") or ""),
            "resolution_at": approved_at.strip(),
            "resolution_actor_id": actor,
            "resolution_observation_fingerprint": str(
                row_metadata.get("resolution_observation_fingerprint") or ""
            ),
        }
    )


def match_reopen_item(
    row: Mapping[str, Any],
    authorization: AuthorizationSnapshot,
    template: Mapping[str, Any],
    *,
    user: Mapping[str, Any],
) -> DirectActionContract | None:
    """Reopen is bound to the approval record, never to the current observation."""
    try:
        if template.get("template_id") != REOPEN_EXCEPTION_TEMPLATE_ID:
            return None
        if not _actor_matches(user, authorization):
            return None
        item_id = str(row.get("item_id") or "").strip()
        if not item_id or not _scope_matches(
            (row.get("tenant_id"), row.get("workspace_id")), authorization
        ):
            return None
        if not _owner_allowed(row, authorization) or workflow_is_quarantined(row):
            return None
        if not business_cartridge_allowed(
            user, str(row.get("cartridge_id") or ""), allow_platform=True
        ):
            return None
        if _status(row.get("status")) != "dismissed" or not workflow_reopen_allowed(
            row
        ):
            return None
        digest = resolution_digest(row)
        if digest is None:
            return None
        return _contract(
            item=dict(row),
            item_id=item_id,
            template=template,
            authorization=authorization,
            cartridge_id=str(row.get("cartridge_id") or "").strip(),
            decision_id=None,
            state="dismissed",
            observation_fingerprint=digest,
            evidence=digest,
        )
    except (TypeError, ValueError):
        return None


def direct_contract_from_persisted_row(
    row: Mapping[str, Any],
    *,
    authorization: AuthorizationSnapshot,
    template: Mapping[str, Any],
    user: Mapping[str, Any],
) -> DirectActionContract | None:
    if template.get("template_id") == REOPEN_EXCEPTION_TEMPLATE_ID:
        return match_reopen_item(row, authorization, template, user=user)
    if template.get("template_id") not in DIRECT_ACTION_TEMPLATE_IDS:
        return None
    item_id = str(row.get("item_id") or "")
    item = persisted_item(row, item_id)
    if item is None:
        return None
    return match_direct_action_item(
        {**item, "status": row.get("status"), "decision_id": row.get("decision_id")},
        row,
        authorization,
        template,
        user=user,
    )


def resolution_fingerprints_valid(fingerprint: str, evidence: str) -> bool:
    return bool(_HEX_DIGEST.fullmatch(fingerprint) and _HEX_DIGEST.fullmatch(evidence))


def token_matches_direct_contract(
    token: Mapping[str, Any], contract: DirectActionContract
) -> bool:
    return str(token.get("template_id") or "") == contract.template_id and all(
        str(token.get(key) or "") == value
        for key, value in (
            ("item_id", contract.item_id),
            ("binding_digest", contract.binding_digest),
            ("evidence_digest", contract.evidence_digest),
            ("observation_fingerprint", contract.observation_fingerprint),
            ("contract_digest", contract.contract_digest),
            ("target_digest", contract.target_digest),
            ("decision_digest", contract.decision_digest),
            ("access_revision_digest", contract.access_revision_digest),
            ("rbac_policy_digest", contract.rbac_policy_digest),
        )
    )


__all__ = (
    "DIRECT_CONTRACT_VERSION",
    "DirectActionContract",
    "DirectMatch",
    "EXCEPTION_RESOLUTION",
    "LIVE_BOUND_TEMPLATE_IDS",
    "MONITOR_ITEM_KINDS",
    "NO_DECISION_DIGEST",
    "RESOLUTION_KEYS",
    "classify_direct_action",
    "direct_contract_from_persisted_row",
    "direct_evidence_digest",
    "live_direct_candidate",
    "match_direct_action_item",
    "match_reopen_item",
    "resolution_digest",
    "resolution_fingerprints_valid",
    "studio_href",
    "token_matches_direct_contract",
    "user_can_view_studio",
)
