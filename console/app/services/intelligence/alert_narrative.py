"""Mission 5: one persisted monitor alert in, its narrative out.

Two callers build narratives and they must agree on what an alert "is":

* the deferred narrator, which asks the LLM for a framing sentence and stores
  the result in ``control_room_items.metadata.narrative``;
* the Control Room read path, which publishes a narrative on ``/control-room``.

Both rebuild the narrative inputs here, from the persisted row, so the
``source_fingerprint`` the narrator stored and the one the reader recomputes
come from the same fields.

Why stored narratives are signed
--------------------------------
``control_room_items.metadata`` is writable by mcp-infra, so a sentence found
there proves nothing about who wrote it. The narrator therefore signs what it
stores with console's evidence keyring, under a purpose of its own and bound to
the item, the scope and the occurrence fingerprint. The reader reuses a stored
sentence only when that signature verifies; anything else is treated as if no
narrative had been stored, and the page shows the template. Even a verified
sentence is re-validated (``narrative_service.reconcile_narrative``), and every
other field is recomputed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

from app.services.control_room.business_evidence_signing import (
    active_evidence_signing_key_id,
    sign_control_room_evidence,
    verify_control_room_evidence,
)
from app.services.intelligence.narrative_service import (
    build_narrative,
    reconcile_narrative,
)

logger = logging.getLogger(__name__)

NARRATIVE_METADATA_KEY = "narrative"
NARRATIVE_ATTESTATION_KEY = "attestation"
NARRATIVE_ATTESTATION_PURPOSE = "control-room-alert-narrative-v1"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def narrative_inputs(
    *, domain: Any, severity: Any, metadata: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(alert_payload, engine_result)`` for the narrative service.

    Built from what ``control_room__raise_analysis_alert`` persisted: the row's
    own domain and severity columns, and the ``analysis_evidence`` block, whose
    ``metrics`` are the ones ``agent_runtime._monitor_alert_args`` computed.
    """
    analysis = _mapping(metadata.get("analysis_evidence"))
    blockers = analysis.get("blockers")
    payload = {
        "domain": str(domain or "").strip(),
        "severity": str(severity or "").strip().lower(),
        "alert_type": metadata.get("alert_type"),
        "engine": analysis.get("engine"),
        "metrics": dict(_mapping(analysis.get("metrics"))),
        "blockers": list(blockers) if isinstance(blockers, list) else [],
    }
    return payload, dict(analysis)


def _signed_payload(
    narrative: Mapping[str, Any], *, item_id: Any, tenant_id: Any, workspace_id: Any
) -> bytes:
    return json.dumps(
        {
            "item_id": str(item_id or ""),
            "tenant_id": str(tenant_id or ""),
            "workspace_id": str(workspace_id or ""),
            "status": narrative.get("status"),
            "reason": narrative.get("reason"),
            "explanation": narrative.get("explanation"),
            "source_fingerprint": narrative.get("source_fingerprint"),
            "narrator_version": narrative.get("narrator_version"),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def attest_narrative(
    narrative: Mapping[str, Any], *, item_id: Any, tenant_id: Any, workspace_id: Any
) -> dict[str, Any]:
    """The narrative to store, signed by console. Never raises.

    Without a usable keyring the narrative is stored unsigned, which the reader
    treats exactly like no narrative at all.
    """
    unsigned = {
        key: value for key, value in narrative.items() if key != NARRATIVE_ATTESTATION_KEY
    }
    try:
        key_id = active_evidence_signing_key_id()
        signature = sign_control_room_evidence(
            _signed_payload(
                unsigned, item_id=item_id, tenant_id=tenant_id, workspace_id=workspace_id
            ),
            purpose=NARRATIVE_ATTESTATION_PURPOSE,
            key_id=key_id,
        )
    except Exception as exc:  # noqa: BLE001 - unsigned is simply never trusted
        logger.warning(
            "narrative: signing unavailable error_code=%s", type(exc).__name__
        )
        return unsigned
    return {
        **unsigned,
        NARRATIVE_ATTESTATION_KEY: {"key_id": key_id, "signature": signature},
    }


def verified_stored_narrative(
    stored: Any, *, item_id: Any, tenant_id: Any, workspace_id: Any
) -> dict[str, Any] | None:
    """The stored narrative only if console signed it for this item and scope."""
    if not isinstance(stored, Mapping):
        return None
    attestation = stored.get(NARRATIVE_ATTESTATION_KEY)
    if not isinstance(attestation, Mapping):
        return None
    key_id = str(attestation.get("key_id") or "")
    signature = str(attestation.get("signature") or "")
    if not key_id or not signature:
        return None
    if not verify_control_room_evidence(
        _signed_payload(
            stored, item_id=item_id, tenant_id=tenant_id, workspace_id=workspace_id
        ),
        purpose=NARRATIVE_ATTESTATION_PURPOSE,
        key_id=key_id,
        signature=signature,
    ):
        return None
    return dict(stored)


def published_narrative(
    *,
    domain: Any,
    severity: Any,
    metadata: Mapping[str, Any],
    item_id: Any,
    tenant_id: Any,
    workspace_id: Any,
) -> dict[str, Any]:
    """The narrative for the alert as it stands now. Synchronous, never raises."""
    payload, engine_result = narrative_inputs(
        domain=domain, severity=severity, metadata=metadata
    )
    stored = verified_stored_narrative(
        metadata.get(NARRATIVE_METADATA_KEY),
        item_id=item_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    return reconcile_narrative(payload, stored, engine_result=engine_result)


async def narrate_alert(
    *,
    domain: Any,
    severity: Any,
    metadata: Mapping[str, Any],
    llm_caller: Callable[[str], Any] | None,
) -> dict[str, Any]:
    """Fresh narrative, with one LLM framing sentence when a caller is given."""
    payload, engine_result = narrative_inputs(
        domain=domain, severity=severity, metadata=metadata
    )
    return await build_narrative(
        payload, engine_result=engine_result, llm_caller=llm_caller
    )


def evidence_note(signal_count: Any, observed_at: Any) -> str | None:
    """Which figure the alert rests on and when console observed it.

    Both values come from the attested observation, never from prose, so the
    note cannot cite a figure the evidence does not contain.
    """
    if isinstance(signal_count, bool) or not isinstance(signal_count, int):
        return None
    if signal_count <= 0:
        return None
    observed = str(observed_at or "").strip()
    try:
        date.fromisoformat(observed)
    except ValueError:
        return None
    if signal_count == 1:
        return (
            "Evidencia verificada por el servidor: 1 senal del monitor "
            f"observada el {observed}."
        )
    return (
        f"Evidencia verificada por el servidor: {signal_count} senales del "
        f"monitor observadas el {observed}."
    )


__all__ = (
    "NARRATIVE_ATTESTATION_KEY",
    "NARRATIVE_ATTESTATION_PURPOSE",
    "NARRATIVE_METADATA_KEY",
    "attest_narrative",
    "evidence_note",
    "narrate_alert",
    "narrative_inputs",
    "published_narrative",
    "verified_stored_narrative",
)
