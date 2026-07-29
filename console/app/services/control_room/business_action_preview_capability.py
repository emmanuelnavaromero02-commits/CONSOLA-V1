from __future__ import annotations

from collections.abc import Collection, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

from fastapi import HTTPException

from app.services.control_room.business_action_authoritative_item import (
    current_authority_claims,
)
from app.services.control_room.business_action_authority_policy import (
    EXECUTABLE_TEMPLATE_ID,
    actor_id,
    authority_scope,
)
from app.services.control_room.business_action_authority_repository import (
    lock_action_binding_token_for_mutation,
)
from app.services.control_room.business_action_authorization_snapshot import (
    capture_authorization_snapshot,
)
from app.services.control_room.business_action_authorization_binding import (
    token_authorization_matches,
)
from app.services.control_room.business_action_authoritative_item import (
    contract_from_persisted_row,
)
from app.services.control_room.business_action_authority_repository import (
    fetch_authoritative_row_for_update,
)
from app.services.control_room.business_action_registry import ACTION_TEMPLATES
from app.services.control_room.business_explicit_action_binding import (
    VerifiedActionBinding,
)


@dataclass(frozen=True)
class PreviewAuthorityCapability:
    values: Mapping[str, Any] = field(repr=False)
    action_handle: str = field(repr=False)


_CAPABILITY: ContextVar[PreviewAuthorityCapability | None] = ContextVar(
    "control_room_preview_authority", default=None
)


def install_preview_authority(
    token_values: Mapping[str, Any], action_handle: str
) -> None:
    _CAPABILITY.set(PreviewAuthorityCapability(dict(token_values), action_handle))


def _changed() -> HTTPException:
    return HTTPException(
        409,
        {
            "code": "item_business_state_changed",
            "message": "control room item changed; reload before mutating",
        },
    )


async def lock_contextual_preview_authority(
    conn: Any,
    *,
    item: Mapping[str, Any],
    user: Mapping[str, Any],
    binding_id: str,
) -> None:
    capability = _CAPABILITY.get()
    if capability is None or capability.action_handle != binding_id:
        return
    item_id = str(item.get("id") or item.get("item_id") or "").strip()
    if not item_id:
        raise _changed()
    token = await lock_action_binding_token_for_mutation(
        conn,
        user=user,
        action_handle=capability.action_handle,
        item_id=item_id,
    )
    tenant_id, workspace_id = authority_scope(user)
    authorization = await capture_authorization_snapshot(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_user_id=actor_id(user),
        permission="control_room.write",
    )
    expected = capability.values
    row = await fetch_authoritative_row_for_update(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        item_id=item_id,
    )
    contract = (
        contract_from_persisted_row(row, authorization=authorization)
        if row is not None and authorization is not None
        else None
    )
    compared = (
        "id",
        "tenant_id",
        "workspace_id",
        "subject_user_id",
        "item_id",
        "template_id",
        "binding_digest",
        "evidence_digest",
        "observation_fingerprint",
        "contract_digest",
        "target_digest",
        "decision_digest",
        "access_revision_digest",
        "rbac_policy_digest",
        "issued_at",
        "expires_at",
        "status",
    )
    if (
        authorization is None
        or contract is None
        or not token_authorization_matches(token, authorization)
        or any(
            str(token.get(key) or "") != str(expected.get(key) or "")
            for key in compared
        )
        or any(
            str(token.get(key) or "") != str(value)
            for key, value in {
                "binding_digest": contract.binding_digest,
                "evidence_digest": contract.evidence_digest,
                "observation_fingerprint": contract.observation_fingerprint,
                "contract_digest": contract.contract_digest,
                "target_digest": contract.target_digest,
                "decision_digest": contract.decision_digest,
            }.items()
        )
    ):
        raise _changed()


def contextual_authority_bindings(
    item: Mapping[str, Any],
    user: Mapping[str, Any],
    *,
    enabled_template_ids: Collection[str] | None,
    clock: Callable[[], datetime] | None,
) -> tuple[VerifiedActionBinding, ...]:
    capability = _CAPABILITY.get()
    if capability is None:
        return ()
    token = capability.values
    try:
        tenant_id, workspace_id = authority_scope(user)
        now = (clock() if clock is not None else datetime.now(UTC)).astimezone(UTC)
        expires_at = token.get("expires_at")
        issued_at = token.get("issued_at")
        template_id = str(token.get("template_id") or "")
        if not (
            isinstance(expires_at, datetime)
            and isinstance(issued_at, datetime)
            and issued_at.astimezone(UTC) <= now < expires_at.astimezone(UTC)
            and token.get("status") == "active"
            and template_id == EXECUTABLE_TEMPLATE_ID
            and (enabled_template_ids is None or template_id in enabled_template_ids)
            and int(token.get("subject_user_id")) == actor_id(user)
            and str(token.get("tenant_id")) == tenant_id
            and str(token.get("workspace_id")) == workspace_id
        ):
            return ()
        template = ACTION_TEMPLATES[EXECUTABLE_TEMPLATE_ID]
        claims = current_authority_claims(item, user, template)
        if claims is None:
            return ()
        comparisons = {
            "item_id": claims["item_id"],
            "binding_digest": claims["binding_digest"],
            "evidence_digest": claims["evidence_digest"],
            "observation_fingerprint": claims["observation_fingerprint"],
            "contract_digest": claims["contract_digest"],
            "target_digest": claims["target_digest"],
            "decision_digest": claims["decision_digest"],
        }
        if any(
            str(token.get(key) or "") != str(value)
            for key, value in comparisons.items()
        ):
            return ()
        values = {
            "version": "control-room-action-authority/v1",
            "template_id": EXECUTABLE_TEMPLATE_ID,
            "binding_id": capability.action_handle,
            "observation_fingerprint": claims["observation_fingerprint"],
            "execution_target_digest": claims["target_digest"],
            "template_contract_digest": claims["template_contract_digest"],
            "attestation_key_id": "digest-only-authority",
            "issued_at": issued_at.astimezone(UTC).isoformat(),
            "expires_at": expires_at.astimezone(UTC).isoformat(),
        }
        return (VerifiedActionBinding(values),)
    except (TypeError, ValueError):
        return ()


__all__ = (
    "contextual_authority_bindings",
    "install_preview_authority",
    "lock_contextual_preview_authority",
)
