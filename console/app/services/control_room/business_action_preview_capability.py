from __future__ import annotations

from collections.abc import Collection, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

from app.services.control_room.business_action_authoritative_item import (
    current_authority_claims,
)
from app.services.control_room.business_action_authority_policy import (
    EXECUTABLE_TEMPLATE_ID,
    actor_id,
    authority_scope,
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


__all__ = ("contextual_authority_bindings", "install_preview_authority")
