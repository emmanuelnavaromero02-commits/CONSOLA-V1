from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from app.services.control_room.business_action_runtime_contract import (
    runtime_action_digests,
)
from app.services.control_room.business_workflow_provenance import (
    ELIGIBILITY_POLICY_VERSION,
    business_observation_fingerprint,
)


def effective_action_key(
    *,
    workspace_id: str,
    item: Mapping[str, Any],
    template_id: str,
    operation: str,
    provided: str | None = None,
) -> str:
    """Derive one server-owned key for a business execution contract."""
    del provided
    contract = {
        "version": 2,
        "policy_version": ELIGIBILITY_POLICY_VERSION,
        "workspace_id": str(workspace_id or "").strip(),
        "item_id": str(item.get("id") or item.get("item_id") or "").strip(),
        "fingerprint": business_observation_fingerprint(item),
        "decision_id": str(item.get("decision_id") or ""),
        "template_id": str(template_id or "").strip(),
        "operation": str(operation or "").strip(),
        **runtime_action_digests(item, template_id=template_id),
    }
    required = ("workspace_id", "item_id", "fingerprint", "template_id", "operation")
    if not all(contract[key] for key in required):
        raise ValueError("action reservation contract is incomplete")
    payload = json.dumps(contract, separators=(",", ":"), sort_keys=True).encode()
    return f"cr-action:v2:{hashlib.sha256(payload).hexdigest()}"


__all__ = ("effective_action_key",)
