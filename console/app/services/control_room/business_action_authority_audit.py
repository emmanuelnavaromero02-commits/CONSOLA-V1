from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_action_digest import action_contract_digest
from app.services.control_room.business_explicit_action_binding import (
    VerifiedActionBinding,
)


def authority_audit(
    binding: VerifiedActionBinding,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    values = binding.values
    audit = {
        "version": "control-room-authority-audit/v1",
        "key_id": str(values.get("attestation_key_id") or ""),
        "issued_at": str(values.get("issued_at") or ""),
        "expires_at": str(values.get("expires_at") or ""),
        "observation_fingerprint": str(values.get("observation_fingerprint") or ""),
        "execution_target_digest": binding.execution_target_digest,
        "template_contract_digest": binding.template_contract_digest,
        "input_payload_digest": action_contract_digest(dict(payload)),
    }
    if audit["key_id"] != "digest-only-authority":
        audit["binding_id"] = binding.binding_id
    return audit


__all__ = ("authority_audit",)
