from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.services.control_room.business_action_authority import (
    action_item_is_current,
    action_item_is_stale,
    action_source_binding_complete,
)
from app.services.control_room.business_action_authority_policy import (
    EXECUTABLE_TEMPLATE_ID,
    actor_id,
    authority_scope,
    decision_contract_digest,
)
from app.services.control_room.business_action_authority_evidence import (
    evidence_digest,
    metadata,
    persisted_item,
)
from app.services.control_room.business_action_authorization_snapshot import (
    AuthorizationSnapshot,
)
from app.services.control_room.business_action_digest import action_contract_digest
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_eligibility import classify_business_item
from app.services.control_room.business_evidence import has_evidence
from app.services.control_room.business_execution_precondition import dry_run_contract
from app.services.control_room.business_execution_target import (
    execution_target_contract,
    execution_target_digest,
)
from app.services.control_room.business_fingerprint import (
    business_observation_fingerprint,
)
from app.services.control_room.business_template_contract import (
    template_contract_digest,
)
from app.services.control_room.business_workflow_provenance import (
    CURRENT_ELIGIBILITY_FINGERPRINT_KEY,
)


@dataclass(frozen=True, repr=False)
class AuthorityItemContract:
    item: Mapping[str, Any]
    item_id: str
    maker_user_id: int
    tenant_id: str
    workspace_id: str
    decision_id: int
    binding_digest: str
    evidence_digest: str
    observation_fingerprint: str
    template_contract_digest: str
    target_digest: str
    decision_digest: str
    contract_digest: str
    access_revision_digest: str
    rbac_policy_digest: str

    def dry_run_digest(self) -> str:
        return action_contract_digest(
            dry_run_contract(self.item, template_id=EXECUTABLE_TEMPLATE_ID)
        )


def current_authority_claims(
    item: Mapping[str, Any],
    user: Mapping[str, Any],
    template: Mapping[str, Any],
) -> dict[str, Any] | None:
    try:
        tenant_id, workspace_id = authority_scope(user)
        item_id = str(item.get("id") or item.get("item_id") or "").strip()
        if not item_id or any(
            str(value or "") != expected
            for value, expected in (
                (item.get("tenant_id"), tenant_id),
                (item.get("workspace_id"), workspace_id),
            )
        ):
            return None
        if not (
            template.get("template_id") == EXECUTABLE_TEMPLATE_ID
            and action_item_is_current(item, operation="preview")
            and not action_item_is_stale(item)
            and action_source_binding_complete(item)
            and classify_business_item(item).eligible
            and has_evidence(item)
        ):
            return None
        evidence = evidence_digest(item)
        if evidence is None:
            return None
        decision_id = int(item.get("decision_id"))
        target = execution_target_contract(item, template).get("target")
        if target != {
            "system": "omega_control_room",
            "adapter": "internal_followup_task",
            "resource": "decision_actions",
        }:
            return None
        template_digest = template_contract_digest(template)
        claims = {
            "item_id": item_id,
            "maker_user_id": actor_id(user),
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "decision_id": decision_id,
            "evidence_digest": evidence,
            "observation_fingerprint": business_observation_fingerprint(item),
            "template_contract_digest": template_digest,
            "target_digest": execution_target_digest(item, template),
            "decision_digest": decision_contract_digest(workspace_id, decision_id),
        }
        binding = {
            "version": "control-room-action-authority/v1",
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "maker_user_id": claims["maker_user_id"],
            "item_id": item_id,
            "template_id": EXECUTABLE_TEMPLATE_ID,
            "observation_fingerprint": claims["observation_fingerprint"],
            "evidence_digest": evidence,
            "template_contract_digest": template_digest,
            "target_digest": claims["target_digest"],
            "decision_digest": claims["decision_digest"],
        }
        binding_digest = action_contract_digest(binding)
        return {
            **claims,
            "binding_digest": binding_digest,
            "contract_digest": action_contract_digest(
                {**binding, "binding_digest": binding_digest}
            ),
        }
    except (TypeError, ValueError):
        return None


def match_authoritative_item(
    live: Mapping[str, Any],
    persisted_row: Mapping[str, Any],
    authorization: AuthorizationSnapshot,
    template: Mapping[str, Any],
) -> AuthorityItemContract | None:
    try:
        tenant_id = authorization.tenant_id
        workspace_id = authorization.workspace_id
        maker_user_id = authorization.actor_user_id
        if authorization.permission != "control_room.write":
            return None
        item_id = str(live.get("id") or live.get("item_id") or "").strip()
        if not item_id or str(persisted_row.get("item_id") or "") != item_id:
            return None
        if any(
            str(value or "") != expected
            for value, expected in (
                (live.get("tenant_id"), tenant_id),
                (live.get("workspace_id"), workspace_id),
                (persisted_row.get("tenant_id"), tenant_id),
                (persisted_row.get("workspace_id"), workspace_id),
                (persisted_row.get("decision_workspace_id"), workspace_id),
            )
        ):
            return None
        persisted_owner = persisted_row.get("owner_user_id")
        live_owner = live.get("owner_user_id")
        if (
            live_owner is not None and str(persisted_owner or "") != str(live_owner)
        ) or (
            not authorization.workspace_wide
            and str(persisted_owner or "") != str(maker_user_id)
        ):
            return None
        if not (
            template.get("template_id") == EXECUTABLE_TEMPLATE_ID
            and action_item_is_current(live, operation="preview")
            and not action_item_is_stale(live)
            and action_source_binding_complete(live)
            and classify_business_item(live).eligible
            and has_evidence(live)
        ):
            return None
        persisted = persisted_item(persisted_row, item_id)
        if persisted is None or not classify_business_item(persisted).eligible:
            return None
        live_fingerprint = business_observation_fingerprint(live)
        persisted_fingerprint = business_observation_fingerprint(persisted)
        stored_fingerprint = str(
            metadata(persisted_row).get(CURRENT_ELIGIBILITY_FINGERPRINT_KEY) or ""
        )
        if not live_fingerprint or not (
            live_fingerprint == persisted_fingerprint == stored_fingerprint
        ):
            return None
        live_evidence = evidence_digest(live)
        persisted_evidence = evidence_digest(persisted)
        if live_evidence is None or live_evidence != persisted_evidence:
            return None
        decision_id = int(persisted_row.get("decision_id"))
        if int(live.get("decision_id") or 0) != decision_id:
            return None
        target_contract = execution_target_contract(persisted, template)
        target = target_contract.get("target")
        if not isinstance(target, Mapping) or target != {
            "system": "omega_control_room",
            "adapter": "internal_followup_task",
            "resource": "decision_actions",
        }:
            return None
        template_digest = template_contract_digest(template)
        target_digest = execution_target_digest(persisted, template)
        decision_digest = decision_contract_digest(workspace_id, decision_id)
        binding = {
            "version": "control-room-action-authority/v1",
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "maker_user_id": maker_user_id,
            "item_id": item_id,
            "template_id": EXECUTABLE_TEMPLATE_ID,
            "observation_fingerprint": live_fingerprint,
            "evidence_digest": live_evidence,
            "template_contract_digest": template_digest,
            "target_digest": target_digest,
            "decision_digest": decision_digest,
        }
        binding_digest = action_contract_digest(binding)
        return AuthorityItemContract(
            persisted,
            item_id,
            maker_user_id,
            tenant_id,
            workspace_id,
            decision_id,
            binding_digest,
            live_evidence,
            live_fingerprint,
            template_digest,
            target_digest,
            decision_digest,
            action_contract_digest({**binding, "binding_digest": binding_digest}),
            authorization.access_revision_digest,
            authorization.rbac_policy_digest,
        )
    except (TypeError, ValueError):
        return None


def contract_from_persisted_row(
    row: Mapping[str, Any], *, authorization: AuthorizationSnapshot
) -> AuthorityItemContract | None:
    item_id = str(row.get("item_id") or "")
    item = persisted_item(row, item_id)
    tenant_id = str(row.get("tenant_id") or "")
    workspace_id = str(row.get("workspace_id") or "")
    if (
        item is None
        or not tenant_id
        or not workspace_id
        or authorization.permission != "control_room.write"
        or authorization.tenant_id != tenant_id
        or authorization.workspace_id != workspace_id
    ):
        return None
    return match_authoritative_item(
        item,
        row,
        authorization,
        ACTION_TEMPLATES[EXECUTABLE_TEMPLATE_ID],
    )


__all__ = (
    "AuthorityItemContract",
    "contract_from_persisted_row",
    "current_authority_claims",
    "match_authoritative_item",
)
