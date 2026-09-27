from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
from app.services.permissions import has_permission, user_role


DIRECT_CONTRACT_VERSION = "control-room-direct-action/v1"
NO_DECISION_DIGEST = action_contract_digest(
    {"version": DIRECT_CONTRACT_VERSION, "decision": "none"}
)
EXCEPTION_RESOLUTION = "exception_approved"
STUDIO_TAB = "capas"
_PLATFORM_ADMIN_ROLES = frozenset({"owner", "super_admin", "admin"})
_OPEN_STATUSES = frozenset({"open", "in_review"})
_STUDIO_STATUSES = frozenset({"open", "in_review", "decision_created"})
_CARTRIDGE_ID = re.compile(r"^[a-z0-9_]{1,120}$")
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
    return user_role(dict(user)) in _PLATFORM_ADMIN_ROLES and has_permission(
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


def _template_state_allowed(
    template_id: str,
    *,
    row: Mapping[str, Any],
    live_status: str,
    persisted_status: str,
    decision_id: int | None,
    user: Mapping[str, Any],
    cartridge_id: str,
) -> bool:
    row_metadata = metadata(row)
    if template_id in {APPROVE_EXCEPTION_TEMPLATE_ID, DECISION_PROPOSAL_TEMPLATE_ID}:
        return bool(
            persisted_status in _OPEN_STATUSES
            and decision_id is None
            and workflow_columns_unlinked(row)
            and DECISION_PROVENANCE_KEY not in row_metadata
        )
    if template_id == REOPEN_EXCEPTION_TEMPLATE_ID:
        return bool(
            persisted_status == "dismissed"
            and live_status == "dismissed"
            and workflow_reopen_allowed(row)
            and row_metadata.get("resolution") == EXCEPTION_RESOLUTION
        )
    if template_id == OPEN_IN_STUDIO_TEMPLATE_ID:
        return bool(
            persisted_status in _STUDIO_STATUSES
            and _CARTRIDGE_ID.fullmatch(cartridge_id)
            and user_can_view_studio(user)
            and business_cartridge_allowed(user, cartridge_id)
        )
    return False


def _target(template_id: str, cartridge_id: str) -> dict[str, str]:
    if template_id == OPEN_IN_STUDIO_TEMPLATE_ID:
        return {"system": "omega_studio", "cartridge": cartridge_id, "tab": STUDIO_TAB}
    return dict(_TARGETS[template_id])


def match_direct_action_item(
    live: Mapping[str, Any],
    row: Mapping[str, Any],
    authorization: AuthorizationSnapshot,
    template: Mapping[str, Any],
    *,
    user: Mapping[str, Any],
) -> DirectActionContract | None:
    try:
        template_id = str(template.get("template_id") or "")
        if template_id not in DIRECT_ACTION_TEMPLATE_IDS:
            return None
        if authorization.permission != "control_room.write":
            return None
        tenant_id = authorization.tenant_id
        workspace_id = authorization.workspace_id
        maker_user_id = authorization.actor_user_id
        if optional_actor_id(user.get("id")) != maker_user_id:
            return None
        item_id = str(live.get("id") or live.get("item_id") or "").strip()
        if not item_id or str(row.get("item_id") or "") != item_id:
            return None
        if any(
            str(value or "") != expected
            for value, expected in (
                (live.get("tenant_id"), tenant_id),
                (live.get("workspace_id"), workspace_id),
                (row.get("tenant_id"), tenant_id),
                (row.get("workspace_id"), workspace_id),
            )
        ):
            return None
        decision_id = _decision_id(row)
        if decision_id is not None and (
            str(row.get("decision_workspace_id") or "") != workspace_id
            or int(live.get("decision_id") or 0) != decision_id
        ):
            return None
        if decision_id is None and live.get("decision_id") not in (None, 0):
            return None
        persisted_owner = row.get("owner_user_id")
        live_owner = live.get("owner_user_id")
        if (
            live_owner is not None and str(persisted_owner or "") != str(live_owner)
        ) or (
            not authorization.workspace_wide
            and str(persisted_owner or "") != str(maker_user_id)
        ):
            return None
        if (
            action_item_is_stale(live)
            or not action_source_binding_complete(live)
            or not classify_business_item(live).eligible
            or not has_evidence(live)
            or workflow_is_quarantined(row)
        ):
            return None
        persisted = persisted_item(row, item_id)
        if persisted is None or not classify_business_item(persisted).eligible:
            return None
        live_status = _status(live.get("status"))
        persisted_status = _status(row.get("status"))
        if live_status != persisted_status:
            return None
        cartridge_id = str(row.get("cartridge_id") or "").strip()
        live_cartridge = str(live.get("cartridge") or live.get("cartridge_id") or "")
        if live_cartridge.strip() and live_cartridge.strip() != cartridge_id:
            return None
        if not _template_state_allowed(
            template_id,
            row=row,
            live_status=live_status,
            persisted_status=persisted_status,
            decision_id=decision_id,
            user=user,
            cartridge_id=cartridge_id,
        ):
            return None
        live_fingerprint = business_observation_fingerprint(live)
        persisted_fingerprint = business_observation_fingerprint(persisted)
        stored_fingerprint = str(
            metadata(row).get(CURRENT_ELIGIBILITY_FINGERPRINT_KEY) or ""
        )
        if not live_fingerprint or not (
            live_fingerprint == persisted_fingerprint == stored_fingerprint
        ):
            return None
        live_evidence = direct_evidence_digest(live)
        if live_evidence is None or live_evidence != direct_evidence_digest(persisted):
            return None
        target = _target(template_id, cartridge_id)
        template_digest = template_contract_digest(template)
        target_digest = action_contract_digest(
            {
                "version": DIRECT_CONTRACT_VERSION,
                "template_id": template_id,
                "target": target,
            }
        )
        decision_digest = (
            decision_contract_digest(workspace_id, decision_id)
            if decision_id is not None
            else NO_DECISION_DIGEST
        )
        binding = {
            "version": DIRECT_CONTRACT_VERSION,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "maker_user_id": maker_user_id,
            "item_id": item_id,
            "template_id": template_id,
            "status": persisted_status,
            "observation_fingerprint": live_fingerprint,
            "evidence_digest": live_evidence,
            "template_contract_digest": template_digest,
            "target_digest": target_digest,
            "decision_digest": decision_digest,
        }
        binding_digest = action_contract_digest(binding)
        return DirectActionContract(
            item=persisted,
            item_id=item_id,
            template_id=template_id,
            maker_user_id=maker_user_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            cartridge_id=cartridge_id,
            decision_id=decision_id,
            binding_digest=binding_digest,
            evidence_digest=live_evidence,
            observation_fingerprint=live_fingerprint,
            template_contract_digest=template_digest,
            target_digest=target_digest,
            decision_digest=decision_digest,
            contract_digest=action_contract_digest(
                {**binding, "binding_digest": binding_digest}
            ),
            access_revision_digest=authorization.access_revision_digest,
            rbac_policy_digest=authorization.rbac_policy_digest,
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
    "EXCEPTION_RESOLUTION",
    "NO_DECISION_DIGEST",
    "direct_contract_from_persisted_row",
    "direct_evidence_digest",
    "match_direct_action_item",
    "studio_href",
    "token_matches_direct_contract",
    "user_can_view_studio",
)
