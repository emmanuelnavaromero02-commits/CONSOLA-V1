"""Control Room advisory tools for monitor agents."""
from __future__ import annotations

import hashlib
import json
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
import httpx
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
_MAX_ANALYSIS_BYTES = 8000
_CONSOLE_URL = (
    os.environ.get("CONSOLE_INTERNAL_URL")
    or os.environ.get("CONSOLE_URL")
    or "http://console:8000"
).rstrip("/")
_ANALYSIS_ENGINES = {
    "monte_carlo",
    "bayesian_calibration",
    "wisdom_bit",
    "decision_orchestrator",
}


def _is_production() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _console_headers() -> dict[str, str]:
    key = os.environ.get("INTERNAL_API_KEY_MCP_INFRA_TO_CONSOLE")
    if not key:
        if _is_production():
            raise HTTPException(
                500,
                "missing INTERNAL_API_KEY_MCP_INFRA_TO_CONSOLE for internal Console call",
            )
        key = os.environ.get("INTERNAL_API_KEY") or ""
    if not key:
        raise HTTPException(500, "missing internal Console API key")
    return {"x-api-key": key, "x-internal-service": "mcp-infra"}


async def _call_console(path: str, payload: dict[str, Any], *, timeout: float = 60.0) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=_console_headers()) as client:
            response = await client.post(f"{_CONSOLE_URL}{path}", json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Console internal request failed: {exc}") from exc
    if response.status_code >= 400:
        detail: Any
        try:
            detail = response.json()
        except Exception:
            detail = response.text[:500]
        raise HTTPException(response.status_code, detail)
    data = response.json()
    if not isinstance(data, dict):
        raise HTTPException(502, "Console internal response must be an object")
    return data


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


def _bounded_payload(value: Any, label: str, *, max_bytes: int = _MAX_ANALYSIS_BYTES) -> Any:
    clean = _redact_sensitive(value if value is not None else {})
    raw = json.dumps(clean, sort_keys=True, default=str, ensure_ascii=False)
    if len(raw.encode("utf-8")) > max_bytes:
        raise HTTPException(400, f"{label} payload is too large")
    return clean


def _analysis_evidence(
    *,
    analysis_type: str,
    engine: str,
    engine_run_id: str,
    confidence: float,
    p10: Any = None,
    p50: Any = None,
    p90: Any = None,
    recommended_option: Any = None,
    metrics: Any = None,
    blockers: Any = None,
    distribution: Any = None,
) -> dict[str, Any]:
    engine = _as_safe_key(engine, "engine")
    if engine not in _ANALYSIS_ENGINES:
        raise HTTPException(400, "engine must be one of monte_carlo, bayesian_calibration, wisdom_bit, decision_orchestrator")
    evidence = {
        "analysis_type": _as_safe_key(analysis_type, "analysis_type"),
        "engine": engine,
        "engine_run_id": _as_text(engine_run_id, "engine_run_id", max_len=240),
        "confidence": _as_confidence(confidence),
        "quantiles": {"p10": p10, "p50": p50, "p90": p90},
        "recommended_option": _bounded_payload(recommended_option, "recommended_option"),
        "metrics": _bounded_payload(metrics, "metrics"),
        "blockers": _bounded_payload(blockers if blockers is not None else [], "blockers"),
        "distribution": _bounded_payload(distribution, "distribution"),
    }
    return _bounded_payload(evidence, "analysis_evidence")


def _trusted_agent_scope(
    ctx: dict[str, Any] | None,
    *,
    permission: str = "control_room.write",
) -> dict[str, str]:
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
    if permission not in permissions:
        raise HTTPException(403, f"permission required: {permission}")
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "agent_id": agent_id,
        "agent_slug": str(ctx.get("agent_slug") or ""),
        "agent_name": str(ctx.get("agent_name") or ""),
        "agent_run_id": str(ctx.get("agent_run_id") or ""),
        "email": str(ctx.get("email") or "agent-runner@omega.local"),
    }


def _trusted_read_scope(
    ctx: dict[str, Any] | None,
    *,
    permission: str = "datasets.read",
) -> dict[str, str]:
    if not isinstance(ctx, dict) or not ctx.get("trusted"):
        raise HTTPException(403, "trusted security_context required")
    tenant_id = str(ctx.get("tenant_id") or "").strip()
    workspace_id = str(ctx.get("workspace_id") or "").strip()
    if not tenant_id or not workspace_id:
        raise HTTPException(403, "tenant/workspace scope required")
    permissions = {str(item) for item in (ctx.get("permissions") or [])}
    if permission not in permissions:
        raise HTTPException(403, f"permission required: {permission}")
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "email": str(ctx.get("email") or "mcp-infra@omega.local"),
        "role": str(ctx.get("role") or ""),
    }


async def _read_control_room_view(
    view: str,
    security_context: dict[str, Any] | None,
    *,
    params: dict[str, Any] | None = None,
    permission: str = "datasets.read",
) -> dict[str, Any]:
    scope = _trusted_read_scope(security_context, permission=permission)
    result = await _call_console(
        "/api/control-room/internal/read",
        {
            "security_context": security_context,
            "view": view,
            "params": params or {},
        },
        timeout=45.0,
    )
    return {
        "ok": True,
        "view": view,
        "tenant_id": scope["tenant_id"],
        "workspace_id": scope["workspace_id"],
        "data": result.get("data"),
    }


@tool(
    name="control_room__summary_read",
    description=(
        "Read the scoped Control Room summary for the active tenant/workspace. "
        "This is read-only and does not create decisions, alerts or external actions."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
async def control_room__summary_read(
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _read_control_room_view("summary", security_context)


@tool(
    name="control_room__dashboard_read",
    description=(
        "Read the scoped Control Room dashboard payload, including cards and "
        "visible operational signals. This is read-only."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
async def control_room__dashboard_read(
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _read_control_room_view(
        "dashboard", security_context, permission="operations.read"
    )


@tool(
    name="control_room__ops_summary_read",
    description=(
        "Read the scoped Control Room operational sync summary and latest "
        "pipeline stage state. This is read-only."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
async def control_room__ops_summary_read(
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _read_control_room_view(
        "ops_summary", security_context, permission="operations.read"
    )


@tool(
    name="control_room__alerts_read",
    description=(
        "Read scoped Control Room alerts/items visible to the active "
        "tenant/workspace. This is read-only."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
async def control_room__alerts_read(
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _read_control_room_view(
        "alerts", security_context, permission="operations.read"
    )


@tool(
    name="control_room__agents_ops_read",
    description=(
        "Read scoped AgentOps monitor status used by Control Room. "
        "This is read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
        "additionalProperties": False,
    },
)
async def control_room__agents_ops_read(
    limit: int = 12,
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _read_control_room_view(
        "agents_ops",
        security_context,
        params={"limit": max(1, min(int(limit or 12), 50))},
        permission="operations.read",
    )


@tool(
    name="control_room__sap_successfactors_gold_kpis_read",
    description=(
        "Read scoped SuccessFactors Gold KPI payload for Control Room. "
        "This is read-only."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
async def control_room__sap_successfactors_gold_kpis_read(
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _read_control_room_view(
        "sap_successfactors_gold_kpis",
        security_context,
    )


@tool(
    name="control_room__talent_kpis_read",
    description=(
        "Read scoped SuccessFactors Talent KPIs, feature-pack state and "
        "blockers for Control Room. This is read-only."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
async def control_room__talent_kpis_read(
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _read_control_room_view(
        "sap_successfactors_talent_kpis",
        security_context,
    )


@tool(
    name="control_room__talent_overview_read",
    description=(
        "Read scoped SuccessFactors Talent overview with safe aggregate "
        "coverage and blockers. This is read-only."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
async def control_room__talent_overview_read(
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _read_control_room_view(
        "sap_successfactors_talent_overview",
        security_context,
    )


@tool(
    name="control_room__talent_9box_read",
    description=(
        "Read scoped SuccessFactors Talent 9-box aggregate state and blockers. "
        "This is read-only and does not expose employee PII."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
async def control_room__talent_9box_read(
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _read_control_room_view(
        "sap_successfactors_talent_9box",
        security_context,
    )


@tool(
    name="control_room__talent_metadata_readiness_read",
    description=(
        "Read scoped SuccessFactors Talent metadata readiness for C/P/A, "
        "learning, recruiting and role blockers. This is read-only."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
async def control_room__talent_metadata_readiness_read(
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _read_control_room_view(
        "sap_successfactors_talent_metadata_readiness",
        security_context,
    )


@tool(
    name="control_room__decision_intelligence_runs_read",
    description=(
        "Read scoped decision-intelligence run history for the active "
        "tenant/workspace. This is read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 250}},
        "additionalProperties": False,
    },
)
async def control_room__decision_intelligence_runs_read(
    limit: int = 50,
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _read_control_room_view(
        "decision_intelligence_runs",
        security_context,
        params={"limit": max(1, min(int(limit or 50), 250))},
        permission="operations.read",
    )


@tool(
    name="calibration__bayesian_state",
    description=(
        "Read scoped Bayesian calibration state from Console Intelligence. "
        "This is evidence-only: it does not create observations, recompute "
        "state, execute actions or write back externally."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "calibration_group": {"type": "string"},
            "model_version": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25},
        },
        "additionalProperties": False,
    },
)
async def calibration__bayesian_state(
    calibration_group: str | None = None,
    model_version: str | None = None,
    limit: int = 10,
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scope = _trusted_agent_scope(security_context, permission="datasets.read")
    payload = {
        "calibration_group": calibration_group,
        "model_version": model_version,
        "limit": max(1, min(int(limit or 10), 25)),
    }
    result = await _call_console(
        "/internal/intelligence/calibration/state",
        {"security_context": security_context, "payload": payload},
        timeout=45.0,
    )
    states = result.get("states") if isinstance(result.get("states"), list) else []
    return {
        "ok": True,
        "engine": "bayesian_calibration",
        "agent_run_id": scope["agent_run_id"],
        "result": result,
        "state_count": len(states),
        "calibration_group": calibration_group,
        "model_version": model_version,
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
    name="simulation__monte_carlo_run",
    description=(
        "Run an advisory Monte Carlo analysis through Console Intelligence "
        "using the signed tenant/workspace context. The tool only persists "
        "internal evidence and never writes back externally."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_type": {
                "type": "string",
                "enum": [
                    "signal",
                    "decision_option",
                    "manual_fixture",
                    "backtest_case",
                    "wisdom_bit",
                ],
            },
            "source_id": {"type": "string"},
            "horizon_days": {"type": "integer", "minimum": 1, "maximum": 365},
            "iterations": {"type": "integer", "minimum": 1, "maximum": 10000},
            "seed": {"type": "integer", "minimum": 0},
            "input_variables": {"type": "object"},
            "assumptions": {"type": "object"},
            "use_external_market_context": {"type": "boolean", "default": False},
            "output_metric": {
                "type": "string",
                "enum": ["net_value", "delta", "cost", "delay_days"],
            },
            "breach_threshold": {"type": "number"},
            "breach_direction": {"type": "string", "enum": ["below", "above"]},
            "evidence_refs": {"type": "array"},
            "options": {"type": "array"},
        },
        "required": ["source_type", "source_id", "input_variables"],
        "additionalProperties": False,
    },
)
async def simulation__monte_carlo_run(
    source_type: str,
    source_id: str,
    input_variables: dict[str, Any],
    horizon_days: int = 30,
    iterations: int = 1000,
    seed: int = 0,
    assumptions: dict[str, Any] | None = None,
    use_external_market_context: bool = False,
    output_metric: str = "net_value",
    breach_threshold: float | None = None,
    breach_direction: str | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
    options: list[dict[str, Any]] | None = None,
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scope = _trusted_agent_scope(security_context)
    payload = {
        "source_type": source_type,
        "source_id": source_id,
        "horizon_days": horizon_days,
        "iterations": min(int(iterations), 10_000),
        "seed": int(seed),
        "input_variables": input_variables,
        "assumptions": assumptions or {},
        "use_external_market_context": bool(use_external_market_context),
        "output_metric": output_metric,
        "breach_threshold": breach_threshold,
        "breach_direction": breach_direction,
        "evidence_refs": evidence_refs or [],
        "options": options,
    }
    result = await _call_console(
        "/internal/intelligence/monte-carlo/run",
        {"security_context": security_context, "payload": payload},
        timeout=90.0,
    )
    return {
        "ok": True,
        "engine": "monte_carlo",
        "agent_run_id": scope["agent_run_id"],
        "result": result,
    }


@tool(
    name="decision__orchestrate",
    description=(
        "Create an advisory decision orchestration through Console and "
        "optionally execute supported internal engines. External write-back "
        "remains disabled."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_type": {
                "type": "string",
                "enum": [
                    "control_room_item",
                    "agent_alert",
                    "intelligence_signal",
                    "monte_carlo_simulation",
                    "calibration_observation",
                    "wisdom_bit",
                    "manual_fixture",
                ],
            },
            "source_id": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "metrics": {"type": "object"},
            "entities": {"type": "array"},
            "time_horizon": {"type": "string"},
            "constraints": {"type": "object"},
            "evidence_refs": {"type": "array"},
            "execute_engines": {"type": "boolean"},
            "engine_inputs": {"type": "object"},
        },
        "required": ["source_type", "source_id"],
        "additionalProperties": False,
    },
)
async def decision__orchestrate(
    source_type: str,
    source_id: str,
    title: str | None = None,
    description: str | None = None,
    metrics: dict[str, Any] | None = None,
    entities: list[dict[str, Any]] | None = None,
    time_horizon: str | None = None,
    constraints: dict[str, Any] | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
    execute_engines: bool = True,
    engine_inputs: dict[str, Any] | None = None,
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scope = _trusted_agent_scope(security_context)
    payload = {
        "source_type": source_type,
        "source_id": source_id,
        "title": title,
        "description": description,
        "metrics": metrics or {},
        "entities": entities or [],
        "time_horizon": time_horizon,
        "constraints": constraints or {},
        "evidence_refs": evidence_refs or [],
    }
    result = await _call_console(
        "/internal/intelligence/orchestrate",
        {
            "security_context": security_context,
            "payload": payload,
            "execute_engines": bool(execute_engines),
            "engine_inputs": engine_inputs or {},
        },
        timeout=90.0,
    )
    return {
        "ok": True,
        "engine": "decision_orchestrator",
        "agent_run_id": scope["agent_run_id"],
        "result": result,
    }


@tool(
    name="wisdom_bits__run",
    description=(
        "Evaluate a configured WisdomBit through Console. WB-TALENTO is "
        "recommendation-only, masks roster details and returns blockers when "
        "competency/performance/aspiration data is incomplete."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "wisdom_bit_id": {"type": "string"},
            "cartridge_id": {"type": "string"},
            "payload": {"type": "object"},
        },
        "required": ["wisdom_bit_id"],
        "additionalProperties": False,
    },
)
async def wisdom_bits__run(
    wisdom_bit_id: str,
    cartridge_id: str = "sap_successfactors",
    payload: dict[str, Any] | None = None,
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scope = _trusted_agent_scope(security_context)
    result = await _call_console(
        "/internal/intelligence/wisdom-bits/run",
        {
            "security_context": security_context,
            "wisdom_bit_id": wisdom_bit_id,
            "cartridge_id": cartridge_id,
            "payload": payload or {},
        },
        timeout=45.0,
    )
    return {
        "ok": True,
        "engine": "wisdom_bit",
        "agent_run_id": scope["agent_run_id"],
        "result": result,
    }


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


@tool(
    name="control_room__raise_analysis_alert",
    description=(
        "Create or update an advisory Control Room alert backed by structured "
        "analysis evidence from Monte Carlo, Bayesian calibration, a WisdomBit "
        "or the Decision Orchestrator. It never executes actions or write-back."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "analysis_type": {"type": "string"},
            "engine": {
                "type": "string",
                "enum": [
                    "monte_carlo",
                    "bayesian_calibration",
                    "wisdom_bit",
                    "decision_orchestrator",
                ],
            },
            "engine_run_id": {"type": "string"},
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
            "p10": {"type": ["number", "string", "null"]},
            "p50": {"type": ["number", "string", "null"]},
            "p90": {"type": ["number", "string", "null"]},
            "recommended_option": {"type": "object"},
            "metrics": {"type": "object"},
            "blockers": {"type": "array"},
            "distribution": {"type": "object"},
        },
        "required": [
            "analysis_type",
            "engine",
            "engine_run_id",
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
def control_room__raise_analysis_alert(
    analysis_type: str,
    engine: str,
    engine_run_id: str,
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
    p10: Any = None,
    p50: Any = None,
    p90: Any = None,
    recommended_option: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    blockers: list[Any] | None = None,
    distribution: dict[str, Any] | None = None,
    security_context: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    unexpected = sorted(set(extra) | (set(extra) & _FORBIDDEN_ARGS))
    if unexpected:
        raise HTTPException(400, f"unsupported analysis alert args: {', '.join(unexpected)}")
    scope = _trusted_agent_scope(security_context)
    analysis = _analysis_evidence(
        analysis_type=analysis_type,
        engine=engine,
        engine_run_id=engine_run_id,
        confidence=confidence,
        p10=p10,
        p50=p50,
        p90=p90,
        recommended_option=recommended_option,
        metrics=metrics,
        blockers=blockers,
        distribution=distribution,
    )
    augmented_refs = list(evidence_refs or [])
    augmented_refs.append({
        "kind": "analysis_evidence",
        "engine": analysis["engine"],
        "engine_run_id": analysis["engine_run_id"],
    })
    result = control_room__raise_alert(
        alert_type=alert_type,
        cartridge_id=cartridge_id,
        domain=domain,
        source_dataset=source_dataset,
        entity_key=entity_key,
        entity_label=entity_label,
        title=title,
        message=message,
        severity=severity,
        confidence=confidence,
        recommendation=recommendation,
        impact_estimate=impact_estimate,
        impact_currency=impact_currency,
        evidence_refs=augmented_refs,
        hypothesis=hypothesis,
        expected_outcome=expected_outcome,
        security_context=security_context,
    )
    item_id = str(result["item_id"])
    event_type = "agent_analysis_alert_deduped" if result.get("deduped") else "agent_analysis_alert_created"
    metadata_patch = {
        "origin": analysis["engine"],
        "analysis_type": analysis["analysis_type"],
        "analysis_evidence": analysis,
        "engine_run_id": analysis["engine_run_id"],
    }

    with _conn() as conn, conn.cursor() as cur:
        _set_rls_scope(cur, scope["tenant_id"], scope["workspace_id"])
        cur.execute(
            """
            UPDATE control_room_items
               SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
             WHERE workspace_id = %s::uuid
               AND item_id = %s
            """,
            (Json(metadata_patch), scope["workspace_id"], item_id),
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
                    "engine": analysis["engine"],
                    "engine_run_id": analysis["engine_run_id"],
                    "deduped": bool(result.get("deduped")),
                }),
            ),
        )
        conn.commit()

    return {
        **result,
        "event_type": event_type,
        "engine": analysis["engine"],
        "engine_run_id": analysis["engine_run_id"],
        "analysis_evidence": analysis,
    }
