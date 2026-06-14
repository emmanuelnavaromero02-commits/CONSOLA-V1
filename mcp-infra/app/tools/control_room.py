"""Control Room advisory tools for monitor agents."""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from psycopg2.extras import Json

from app.registry import tool
from app.tools.postgres import _conn


_SEVERITIES = {"low", "medium", "high", "critical"}
_FORBIDDEN_ARGS = {
    "tenant_id",
    "workspace_id",
    "user_id",
    "security_context",
    "execution_status",
    "decision_id",
    "approved_by",
    "outcome",
    "payload",
    "execute",
    "writeback",
    "external_writeback",
}
_TERMINAL_STATUSES = {"dismissed", "resolved", "approved"}
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@/=-]{1,240}$")
_MAX_TEXT = 2000
_MAX_SHORT_TEXT = 240
_MAX_EVIDENCE_REFS = 10
_MAX_EVIDENCE_REF_BYTES = 1000
_MAX_EVIDENCE_TOTAL_BYTES = 4000


def _as_text(value: Any, label: str, *, max_len: int = _MAX_TEXT, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise HTTPException(400, f"{label} is required")
        return None
    text = str(value).strip()
    if not text:
        if required:
            raise HTTPException(400, f"{label} is required")
        return None
    if len(text) > max_len:
        raise HTTPException(400, f"{label} is too long")
    return text


def _as_safe_key(value: Any, label: str) -> str:
    text = _as_text(value, label, max_len=240)
    if text is None or not _SAFE_ID_RE.fullmatch(text):
        raise HTTPException(400, f"{label} contains unsupported characters")
    return text


def _as_severity(value: Any) -> str:
    severity = _as_text(value, "severity", max_len=20)
    assert severity is not None
    severity = severity.lower()
    if severity not in _SEVERITIES:
        raise HTTPException(400, "severity must be one of low, medium, high, critical")
    return severity


def _as_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "confidence must be numeric") from exc
    if confidence < 0 or confidence > 1:
        raise HTTPException(400, "confidence must be between 0 and 1")
    return confidence


def _as_impact(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        impact = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(400, "impact_estimate must be numeric") from exc
    if not impact.is_finite():
        raise HTTPException(400, "impact_estimate must be finite")
    return impact


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("password", "secret", "token", "api_key", "authorization")):
                out[str(key)] = "***"
            else:
                out[str(key)] = _redact_sensitive(item)
        return out
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return value


def _evidence_refs(value: Any) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(400, "evidence_refs must be an array")
    if len(value) > _MAX_EVIDENCE_REFS:
        raise HTTPException(400, "evidence_refs has too many entries")
    refs: list[Any] = []
    total = 0
    for item in value:
        clean = _redact_sensitive(item)
        raw = json.dumps(clean, sort_keys=True, default=str, ensure_ascii=False)
        if len(raw.encode("utf-8")) > _MAX_EVIDENCE_REF_BYTES:
            raise HTTPException(400, "evidence_refs entry is too large")
        total += len(raw.encode("utf-8"))
        if total > _MAX_EVIDENCE_TOTAL_BYTES:
            raise HTTPException(400, "evidence_refs total payload is too large")
        refs.append(clean)
    return refs


def _trusted_agent_scope(ctx: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(ctx, dict) or not ctx.get("trusted"):
        raise HTTPException(403, "trusted security_context required")
    tenant_id = str(ctx.get("tenant_id") or "").strip()
    workspace_id = str(ctx.get("workspace_id") or "").strip()
    agent_id = str(ctx.get("agent_id") or "").strip()
    if not tenant_id or not workspace_id:
        raise HTTPException(403, "tenant/workspace scope required")
    if not agent_id:
        raise HTTPException(403, "agent context required")
    permissions = {str(item) for item in (ctx.get("permissions") or [])}
    if "control_room.write" not in permissions:
        raise HTTPException(403, "permission required: control_room.write")
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "agent_id": agent_id,
        "agent_slug": str(ctx.get("agent_slug") or ""),
        "agent_name": str(ctx.get("agent_name") or ""),
        "agent_run_id": str(ctx.get("agent_run_id") or ""),
        "email": str(ctx.get("email") or "agent-runner@omega.local"),
    }


def _dedup_item_id(*, agent_id: str, workspace_id: str, alert_type: str, entity_key: str, source_dataset: str) -> str:
    raw = json.dumps(
        {
            "agent_id": agent_id,
            "workspace_id": workspace_id,
            "alert_type": alert_type,
            "entity_key": entity_key,
            "source_dataset": source_dataset,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"agent_alert:{digest}"


def _set_rls_scope(cur, tenant_id: str, workspace_id: str) -> None:
    cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
    cur.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id,))
    cur.execute("SELECT set_config('app.platform_admin', 'false', true)")


@tool(
    name="control_room__raise_alert",
    description=(
        "Create or update an advisory Control Room alert for the active "
        "tenant/workspace. Scope is taken from the signed security_context; "
        "the tool cannot execute actions or write back externally."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "alert_type": {"type": "string"},
            "cartridge_id": {"type": "string"},
            "domain": {"type": "string"},
            "source_dataset": {"type": "string"},
            "entity_key": {"type": "string"},
            "entity_label": {"type": "string"},
            "title": {"type": "string"},
            "message": {"type": "string"},
            "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            "confidence": {"type": "number"},
            "recommendation": {"type": "string"},
            "impact_estimate": {"type": "number"},
            "impact_currency": {"type": "string"},
            "evidence_refs": {"type": "array"},
            "hypothesis": {"type": "string"},
            "expected_outcome": {"type": "string"},
        },
        "required": [
            "alert_type",
            "cartridge_id",
            "domain",
            "source_dataset",
            "entity_key",
            "title",
            "message",
            "severity",
            "confidence",
        ],
        "additionalProperties": False,
    },
)
def control_room__raise_alert(
    alert_type: str,
    cartridge_id: str,
    domain: str,
    source_dataset: str,
    entity_key: str,
    title: str,
    message: str,
    severity: str,
    confidence: float,
    entity_label: str | None = None,
    recommendation: str | None = None,
    impact_estimate: float | int | None = None,
    impact_currency: str | None = None,
    evidence_refs: list[Any] | None = None,
    hypothesis: str | None = None,
    expected_outcome: str | None = None,
    security_context: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    unexpected = sorted(set(extra) | (set(extra) & _FORBIDDEN_ARGS))
    if unexpected:
        raise HTTPException(400, f"unsupported alert args: {', '.join(unexpected)}")

    scope = _trusted_agent_scope(security_context)
    alert_type = _as_safe_key(alert_type, "alert_type")
    cartridge_id = _as_safe_key(cartridge_id, "cartridge_id")
    domain = _as_text(domain, "domain", max_len=_MAX_SHORT_TEXT)
    source_dataset = _as_safe_key(source_dataset, "source_dataset")
    entity_key = _as_safe_key(entity_key, "entity_key")
    entity_label = _as_text(entity_label, "entity_label", max_len=_MAX_SHORT_TEXT, required=False)
    title = _as_text(title, "title", max_len=_MAX_SHORT_TEXT)
    message = _as_text(message, "message", max_len=_MAX_TEXT)
    severity = _as_severity(severity)
    confidence = _as_confidence(confidence)
    recommendation = _as_text(recommendation, "recommendation", max_len=_MAX_TEXT, required=False)
    hypothesis = _as_text(hypothesis, "hypothesis", max_len=_MAX_TEXT, required=False)
    expected_outcome = _as_text(expected_outcome, "expected_outcome", max_len=_MAX_TEXT, required=False)
    impact = _as_impact(impact_estimate)
    impact_currency = _as_text(impact_currency or "USD", "impact_currency", max_len=8)
    evidence = _evidence_refs(evidence_refs)

    item_id = _dedup_item_id(
        agent_id=scope["agent_id"],
        workspace_id=scope["workspace_id"],
        alert_type=alert_type,
        entity_key=entity_key,
        source_dataset=source_dataset,
    )
    metadata = {
        "source": "agent",
        "advisory": True,
        "agent_id": scope["agent_id"],
        "agent_slug": scope["agent_slug"],
        "agent_name": scope["agent_name"],
        "agent_run_id": scope["agent_run_id"],
        "alert_type": alert_type,
        "description": message,
        "recommendation": recommendation or "Revisar evidencia y decidir accion supervisada.",
        "hypothesis": hypothesis,
        "expected_outcome": expected_outcome,
        "evidence_refs": evidence,
        "execution_status": "not_started",
        "control_state": {"source": "agent", "advisory": True},
    }

    with _conn() as conn, conn.cursor() as cur:
        _set_rls_scope(cur, scope["tenant_id"], scope["workspace_id"])
        cur.execute(
            """
            SELECT status, metadata
              FROM control_room_items
             WHERE workspace_id = %s::uuid
               AND item_id = %s
             FOR UPDATE
            """,
            (scope["workspace_id"], item_id),
        )
        existing = cur.fetchone()
        previous_metadata = existing[1] if existing and isinstance(existing[1], dict) else {}
        occurrence_count = int(previous_metadata.get("occurrence_count") or 0) + 1
        metadata["occurrence_count"] = occurrence_count
        deduped = existing is not None
        terminal = bool(existing and str(existing[0]) in _TERMINAL_STATUSES)
        event_type = "agent_alert_deduped" if deduped else "agent_alert_created"

        if deduped:
            cur.execute(
                """
                UPDATE control_room_items
                   SET last_seen_at = NOW(),
                       severity = CASE WHEN status = ANY(%s::text[]) THEN severity ELSE %s END,
                       confidence = CASE WHEN status = ANY(%s::text[]) THEN confidence ELSE %s END,
                       priority_score = CASE WHEN status = ANY(%s::text[]) THEN priority_score ELSE %s END,
                       metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
                 WHERE workspace_id = %s::uuid
                   AND item_id = %s
                """,
                (
                    sorted(_TERMINAL_STATUSES),
                    severity,
                    sorted(_TERMINAL_STATUSES),
                    confidence,
                    sorted(_TERMINAL_STATUSES),
                    int(round(confidence * 100)),
                    Json(metadata),
                    scope["workspace_id"],
                    item_id,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO control_room_items (
                    tenant_id, workspace_id, item_id, cartridge_id, domain,
                    source_dataset, item_kind, title, severity, status,
                    entity_kind, entity_id, entity_label, anomaly_type, metadata,
                    impact_estimate, impact_currency, confidence, priority_score,
                    selected_option_id, execution_status
                )
                VALUES (
                    %s::uuid, %s::uuid, %s, %s, %s,
                    %s, 'agent_alert', %s, %s, 'open',
                    'entity', %s, %s, %s, %s::jsonb,
                    %s, %s, %s, %s,
                    NULL, 'not_started'
                )
                """,
                (
                    scope["tenant_id"],
                    scope["workspace_id"],
                    item_id,
                    cartridge_id,
                    domain,
                    source_dataset,
                    title,
                    severity,
                    entity_key,
                    entity_label or entity_key,
                    alert_type,
                    Json(metadata),
                    impact,
                    impact_currency,
                    confidence,
                    int(round(confidence * 100)),
                ),
            )
        cur.execute(
            """
            INSERT INTO control_room_item_events (
                tenant_id, workspace_id, item_id, event_type,
                actor_id, actor_email, metadata
            )
            VALUES (%s::uuid, %s::uuid, %s, %s, NULL, %s, %s::jsonb)
            """,
            (
                scope["tenant_id"],
                scope["workspace_id"],
                item_id,
                event_type,
                scope["email"],
                Json({
                    "source": "agent",
                    "advisory": True,
                    "agent_id": scope["agent_id"],
                    "agent_run_id": scope["agent_run_id"],
                    "deduped": deduped,
                    "terminal_preserved": terminal,
                    "occurrence_count": occurrence_count,
                }),
            ),
        )
        conn.commit()

    return {
        "ok": True,
        "item_id": item_id,
        "created": not deduped,
        "deduped": deduped,
        "terminal_preserved": terminal,
        "occurrence_count": occurrence_count,
        "execution_status": "not_started",
        "advisory": True,
    }
