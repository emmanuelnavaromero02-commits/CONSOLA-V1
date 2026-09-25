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
    return os.environ.get("APP_ENV", "production").strip().lower() in {
        "production",
        "prod",
    }


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


async def _call_console(
    path: str, payload: dict[str, Any], *, timeout: float = 60.0
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(
            timeout=timeout, headers=_console_headers()
        ) as client:
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


def _as_text(
    value: Any, label: str, *, max_len: int = _MAX_TEXT, required: bool = True
) -> str | None:
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


def _lock_scheduled_effect(cur: Any, scope: dict[str, Any], authority: object) -> None:
    if not isinstance(authority, dict) or not {
        "schedule_run_id",
        "fencing_token",
    }.issubset(authority):
        raise HTTPException(403, "scheduled effect authority is required")
    try:
        schedule_run_id = int(authority["schedule_run_id"])
        fencing_token = int(authority["fencing_token"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(403, "scheduled effect authority is invalid") from exc
    try:
        cur.execute(
            "SELECT assert_scheduled_effect_authority(%s,%s,%s::uuid,%s::uuid,%s::uuid)",
            (
                schedule_run_id,
                fencing_token,
                scope["tenant_id"],
                scope["workspace_id"],
                scope["agent_id"],
            ),
        )
    except Exception as exc:
        if getattr(exc, "pgcode", None) == "40001":
            raise HTTPException(409, "scheduled effect authority is stale") from None
        raise
    if cur.fetchone() is None:
        raise HTTPException(409, "scheduled effect authority is stale")


async def _call_console_under_fence(
    scope: dict[str, Any],
    authority: object,
    path: str,
    payload: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    if authority is None:
        return await _call_console(path, payload, timeout=timeout)
    with _conn() as conn, conn.cursor() as cur:
        _set_rls_scope(cur, scope["tenant_id"], scope["workspace_id"])
        _lock_scheduled_effect(cur, scope, authority)
        result = await _call_console(path, payload, timeout=timeout)
        conn.commit()
        return result


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(
                token in lowered
                for token in ("password", "secret", "token", "api_key", "authorization")
            ):
                out[str(key)] = "***"
            else:
                out[str(key)] = _redact_sensitive(item)
        return out
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return value


_SERVER_ATTESTATION_KEYS = frozenset(
    {
        "server_attestation",
        "attestation_key_id",
        "attestation_version",
        "attestation_purpose",
        "scope_binding",
        "business_binding",
        "source_row_hash",
    }
)
_MAX_ATTESTATION_SCAN_DEPTH = 8


def _carries_server_attestation(value: Any, *, depth: int = 0) -> bool:
    if depth > _MAX_ATTESTATION_SCAN_DEPTH:
        return True
    if isinstance(value, dict):
        if any(str(key).strip().lower() in _SERVER_ATTESTATION_KEYS for key in value):
            return True
        return any(
            _carries_server_attestation(item, depth=depth + 1) for item in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_carries_server_attestation(item, depth=depth + 1) for item in value)
    return False


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
        if _carries_server_attestation(item):
            raise HTTPException(400, "evidence_refs cannot carry server attestation")
        clean = _redact_sensitive(item)
        raw = json.dumps(clean, sort_keys=True, default=str, ensure_ascii=False)
        if len(raw.encode("utf-8")) > _MAX_EVIDENCE_REF_BYTES:
            raise HTTPException(400, "evidence_refs entry is too large")
        total += len(raw.encode("utf-8"))
        if total > _MAX_EVIDENCE_TOTAL_BYTES:
            raise HTTPException(400, "evidence_refs total payload is too large")
        refs.append(clean)
    return refs


def _bounded_payload(
    value: Any, label: str, *, max_bytes: int = _MAX_ANALYSIS_BYTES
) -> Any:
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
    for label, value in (
        ("metrics", metrics),
        ("blockers", blockers),
        ("distribution", distribution),
        ("recommended_option", recommended_option),
    ):
        if _carries_server_attestation(value):
            raise HTTPException(400, f"{label} cannot carry server attestation")
    engine = _as_safe_key(engine, "engine")
    if engine not in _ANALYSIS_ENGINES:
        raise HTTPException(
            400,
            "engine must be one of monte_carlo, bayesian_calibration, wisdom_bit, decision_orchestrator",
        )
    evidence = {
        "analysis_type": _as_safe_key(analysis_type, "analysis_type"),
        "engine": engine,
        "engine_run_id": _as_text(engine_run_id, "engine_run_id", max_len=240),
        "confidence": _as_confidence(confidence),
        "quantiles": {"p10": p10, "p50": p50, "p90": p90},
        "recommended_option": _bounded_payload(
            recommended_option, "recommended_option"
        ),
        "metrics": _bounded_payload(metrics, "metrics"),
        "blockers": _bounded_payload(
            blockers if blockers is not None else [], "blockers"
        ),
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


_TOP_N_SCHEMA = {
    "type": "object",
    "properties": {
        "top_n": {
            "type": "integer",
            "minimum": 0,
            "maximum": 10,
            "description": (
                "Opcional. 0 (por defecto) devuelve solo agregados; un valor de 1 a "
                "10 agrega ademas hasta ese numero de filas nombradas con monto "
                "(excepcion controlada al principio de cero filas)."
            ),
        }
    },
    "additionalProperties": False,
}


def _named_rows(top_n: Any) -> int:
    try:
        number = int(top_n or 0)
    except (TypeError, ValueError, OverflowError):
        number = 0
    return max(0, min(number, 10))


@tool(
    name="control_room__finance_kpis_read",
    description=(
        "KPIs financieros agregados del workspace activo: horas facturables "
        "registradas por proyecto (valoradas a tarifa), costo laboral estimado "
        "por departamento del ultimo mes cerrado y margen por proyecto sobre "
        "montos en moneda base. Usala para preguntas de finanzas, rentabilidad o "
        "carga facturable. Cada metrica trae status (ready, degraded, "
        "unavailable), proxy_note con lo que mide y lo que NO mide, y "
        "evidence_refs. NO mide horas aprobadas ni pendientes de facturar, NO es "
        "nomina y NO convierte moneda. Solo agregados; top_n (0-10) devuelve "
        "ademas hasta 10 proyectos nombrados con montos. Solo lectura."
    ),
    input_schema=_TOP_N_SCHEMA,
)
async def control_room__finance_kpis_read(
    top_n: int = 0,
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _read_control_room_view(
        "finance_kpis",
        security_context,
        params={"top_n": _named_rows(top_n)},
    )


@tool(
    name="control_room__operations_kpis_read",
    description=(
        "KPIs operativos agregados del workspace activo: salud de pipelines "
        "(corridas fallidas y exitosas por cartucho en 24 horas y 7 dias, "
        "entidades que mas fallan), frescura de datos por cartucho frente a un "
        "umbral de horas, y tasa de ausentismo por tipo a nivel empresa del "
        "ultimo mes cerrado. Usala para preguntas de operacion de datos o "
        "ausentismo. NO expone texto de errores ni corridas individuales, NO "
        "mide un SLA de negocio (el umbral es parametro) y NO desglosa "
        "ausentismo por unidad organizativa. Solo lectura."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
async def control_room__operations_kpis_read(
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _read_control_room_view(
        "operations_kpis",
        security_context,
    )


@tool(
    name="control_room__risk_kpis_read",
    description=(
        "KPIs de riesgo agregados del workspace activo: poblacion por banda de "
        "riesgo de salida segun el modelo de Talento (con la cifra oficial de "
        "Talento cuando existe), fines de empleo en 30, 60 y 90 dias, y deals de "
        "Salesforce con fecha de cierre vencida (tramos, etapas y motivos). "
        "Usala para preguntas de retencion, vencimientos de empleo o pipeline "
        "comercial en riesgo. NO recalcula el riesgo, NO usa compensacion, NO lee "
        "contratos SAP y NO convierte moneda. Solo agregados; top_n (0-10) "
        "devuelve ademas hasta 10 deals nombrados con monto, nunca el vendedor. "
        "Solo lectura."
    ),
    input_schema=_TOP_N_SCHEMA,
)
async def control_room__risk_kpis_read(
    top_n: int = 0,
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _read_control_room_view(
        "risk_kpis",
        security_context,
        params={"top_n": _named_rows(top_n)},
    )


_SAP_B1_CASE_VIEWS = {
    "caducidad": "sap_b1_expiry_kpis",
    "margen": "sap_b1_margin_kpis",
    "ventas": "sap_b1_sales_kpis",
}


@tool(
    name="control_room__sap_b1_kpis_read",
    description=(
        "KPIs agregados de SAP Business One del workspace activo. case=margen: "
        "margen del grupo con eliminacion intercompania, margen por empresa, "
        "clientes y familias bajo el margen minimo, venta bajo costo, "
        "reconciliacion contra los totales de finanzas y calidad de datos del "
        "ultimo mes cerrado. case=ventas: semaforo por distribuidora con sell-in, "
        "sell-out, crecimiento, sell-through, dias de inventario en canal, margen "
        "y stock expuesto a caducidad. case=caducidad: lotes vencidos y en riesgo "
        "de caducar sin venderse con accion sugerida. Cada metrica trae status, proxy_note con lo que mide "
        "y lo que NO mide, breaches con los incumplimientos de negocio y "
        "evidence_refs. NO convierte monedas. Solo agregados; top_n (0-10) "
        "devuelve ademas hasta 10 clientes nombrados. Solo lectura."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "case": {"type": "string", "enum": sorted(_SAP_B1_CASE_VIEWS)},
            "top_n": {"type": "integer", "minimum": 0, "maximum": 10},
        },
        "required": ["case"],
        "additionalProperties": False,
    },
)
async def control_room__sap_b1_kpis_read(
    case: str = "margen",
    top_n: int = 0,
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    view = _SAP_B1_CASE_VIEWS.get(str(case or "").strip().lower())
    if view is None:
        raise ValueError(f"case must be one of {sorted(_SAP_B1_CASE_VIEWS)}")
    return await _read_control_room_view(
        view,
        security_context,
        params={"top_n": _named_rows(top_n)},
    )


_AGENT_MEMORY_FINDING_TYPES = ("data_gap", "error", "insight", "warning")
_AGENT_MEMORY_SEVERITIES = ("critical", "high", "medium", "low")
_AGENT_MEMORY_SUBJECT_MAX = 200
_AGENT_MEMORY_SUMMARY_MAX = 600
_AGENT_MEMORY_SUMMARY_MAX_WORDS = 60
_AGENT_MEMORY_MAX_EXPIRY_HOURS = 8760


def _agent_memory_subject(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > _AGENT_MEMORY_SUBJECT_MAX:
        raise HTTPException(
            400,
            f"subject must be 1 to {_AGENT_MEMORY_SUBJECT_MAX} characters",
        )
    return text


def _agent_memory_summary(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > _AGENT_MEMORY_SUMMARY_MAX:
        raise HTTPException(
            400,
            f"summary must be 1 to {_AGENT_MEMORY_SUMMARY_MAX} characters",
        )
    if len(text.split()) > _AGENT_MEMORY_SUMMARY_MAX_WORDS:
        raise HTTPException(
            400,
            "summary must be at most "
            f"{_AGENT_MEMORY_SUMMARY_MAX_WORDS} words so it survives the public "
            "projection; write one or two sentences",
        )
    return text


def _agent_memory_choice(value: Any, allowed: tuple[str, ...], field: str) -> str:
    text = str(value or "").strip().lower()
    if text not in allowed:
        raise HTTPException(400, f"{field} must be one of {', '.join(allowed)}")
    return text


def _agent_memory_expiry_hours(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        hours = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise HTTPException(400, "expires_in_hours must be an integer") from exc
    if hours < 1 or hours > _AGENT_MEMORY_MAX_EXPIRY_HOURS:
        raise HTTPException(
            400,
            f"expires_in_hours must be between 1 and {_AGENT_MEMORY_MAX_EXPIRY_HOURS}",
        )
    return hours


@tool(
    name="control_room__agent_memory_read",
    description=(
        "Memoria compartida entre agentes del workspace activo: hallazgos que "
        "otros agentes registraron sobre un tema (un dataset, una columna, una "
        "metrica o una entidad). Usala ANTES de reportar una limitacion de datos "
        "o un problema, para citar lo que ya se sabe en lugar de repetirlo como "
        "nuevo. Cada hallazgo trae el tipo (data_gap, error, insight, warning), "
        "la severidad, el resumen en espanol, cuando se registro, cuando caduca y "
        "el nombre del agente que lo registro. Los hallazgos caducados NO se "
        "devuelven. Es memoria advisory, NO una verdad verificada del origen. "
        "Solo lectura."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "maxLength": _AGENT_MEMORY_SUBJECT_MAX,
                "description": (
                    "Opcional. El tema exacto a consultar, por ejemplo "
                    "cost_center_budget. Sin este parametro devuelve los "
                    "hallazgos activos mas recientes de cualquier tema."
                ),
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "additionalProperties": False,
    },
)
async def control_room__agent_memory_read(
    subject: str | None = None,
    limit: int = 10,
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": max(1, min(int(limit or 10), 20))}
    if subject not in (None, ""):
        params["subject"] = _agent_memory_subject(subject)
    return await _read_control_room_view(
        "agent_memory",
        security_context,
        params=params,
    )


@tool(
    name="control_room__agent_memory_write",
    description=(
        "Registra un hallazgo en la memoria compartida entre agentes del "
        "workspace activo, para que otro agente lo encuentre antes de trabajar "
        "sobre el mismo tema. Usala cuando descubras una limitacion de datos, un "
        "error reproducible, una advertencia o un insight que otro agente "
        "necesitaria saber. Escribe una nota advisory y nada mas: no aprueba, no "
        "ejecuta, no escribe en ningun sistema externo y no puede borrar lo que "
        "otro agente registro. Si ya existe un hallazgo activo tuyo con el mismo "
        "tema y tipo, la llamada no duplica nada. Resumen corto, en espanol de "
        "negocio, sin nombres de personas ni identificadores."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "maxLength": _AGENT_MEMORY_SUBJECT_MAX,
                "description": (
                    "A que aplica el hallazgo: un dataset, una columna, una "
                    "metrica o una entidad. Usa siempre el mismo texto para el "
                    "mismo tema para que otro agente lo encuentre."
                ),
            },
            "finding_type": {
                "type": "string",
                "enum": list(_AGENT_MEMORY_FINDING_TYPES),
            },
            "summary": {
                "type": "string",
                "maxLength": _AGENT_MEMORY_SUMMARY_MAX,
                "description": (
                    "Que encontraste, en una o dos frases de espanol de negocio. "
                    "Maximo 60 palabras: un resumen mas largo se descarta al "
                    "cruzar a otro agente."
                ),
            },
            "severity": {
                "type": "string",
                "enum": list(_AGENT_MEMORY_SEVERITIES),
            },
            "detail": {
                "type": "object",
                "description": (
                    "Opcional. Contexto estructurado para otro agente, por "
                    "ejemplo la metrica afectada."
                ),
            },
            "expires_in_hours": {
                "type": "integer",
                "minimum": 1,
                "maximum": _AGENT_MEMORY_MAX_EXPIRY_HOURS,
                "description": (
                    "Opcional. Cuantas horas sigue vigente el hallazgo. Sin este "
                    "parametro no caduca por si solo."
                ),
            },
        },
        "required": ["subject", "finding_type", "summary"],
        "additionalProperties": False,
    },
)
async def control_room__agent_memory_write(
    subject: str,
    finding_type: str,
    summary: str,
    severity: str = "medium",
    detail: dict[str, Any] | None = None,
    expires_in_hours: int | None = None,
    security_context: dict[str, Any] | None = None,
    effect_authority: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one advisory finding, or report that an active one already exists.

    Writes Postgres directly, the same way control_room__raise_alert does: the
    console internal bridge is read-only by construction (it exposes only
    /internal/read), and adding a write endpoint there would remove that
    property. Tenancy is enforced twice over — the RLS GUCs are set from the
    signed context before the statement runs, and the row carries the same
    tenant/workspace in its own columns, which the table's policy re-checks.
    """
    scope = _trusted_agent_scope(security_context)
    clean_subject = _agent_memory_subject(subject)
    clean_summary = _agent_memory_summary(summary)
    clean_type = _agent_memory_choice(
        finding_type, _AGENT_MEMORY_FINDING_TYPES, "finding_type"
    )
    clean_severity = _agent_memory_choice(
        severity, _AGENT_MEMORY_SEVERITIES, "severity"
    )
    hours = _agent_memory_expiry_hours(expires_in_hours)
    payload_detail = detail if isinstance(detail, dict) else {}

    with _conn() as conn, conn.cursor() as cur:
        _set_rls_scope(cur, scope["tenant_id"], scope["workspace_id"])
        if effect_authority is not None:
            _lock_scheduled_effect(cur, scope, effect_authority)
        cur.execute(
            """
            INSERT INTO agent_shared_findings (
                tenant_id, workspace_id, agent_id, finding_type,
                subject, summary, detail, severity, expires_at
            )
            SELECT %s::uuid, %s::uuid, %s::uuid, %s,
                   %s, %s, %s::jsonb, %s,
                   CASE WHEN %s::int IS NULL THEN NULL
                        ELSE NOW() + (%s::int * INTERVAL '1 hour')
                   END
             WHERE NOT EXISTS (
                SELECT 1
                  FROM agent_shared_findings f
                 WHERE f.tenant_id = %s::uuid
                   AND f.workspace_id = %s::uuid
                   AND f.agent_id = %s::uuid
                   AND f.subject = %s
                   AND f.finding_type = %s
                   AND (f.expires_at IS NULL OR f.expires_at > NOW())
             )
          RETURNING id, created_at, expires_at
            """,
            (
                scope["tenant_id"],
                scope["workspace_id"],
                scope["agent_id"],
                clean_type,
                clean_subject,
                clean_summary,
                Json(payload_detail),
                clean_severity,
                hours,
                hours,
                scope["tenant_id"],
                scope["workspace_id"],
                scope["agent_id"],
                clean_subject,
                clean_type,
            ),
        )
        row = cur.fetchone()
        conn.commit()

    return {
        "ok": True,
        "advisory": True,
        "recorded": row is not None,
        "subject": clean_subject,
        "finding_type": clean_type,
        "severity": clean_severity,
        "agent_name": scope["agent_name"] or None,
        "created_at": (row[1].isoformat() if row and row[1] else None),
        "expires_at": (row[2].isoformat() if row and row[2] else None),
        "note": (
            "hallazgo registrado"
            if row is not None
            else "ya existe un hallazgo activo con el mismo tema y tipo: no se duplico"
        ),
    }


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
    effect_authority: dict[str, Any] | None = None,
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


def _dedup_item_id(
    *,
    agent_id: str,
    workspace_id: str,
    alert_type: str,
    entity_key: str,
    source_dataset: str,
) -> str:
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
            "seed": {
                "type": "integer",
                "minimum": 0,
                "maximum": 9223372036854775807,
            },
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
    effect_authority: dict[str, Any] | None = None,
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
        {
            "security_context": security_context,
            "payload": payload,
            "effect_authority": effect_authority,
        },
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
    effect_authority: dict[str, Any] | None = None,
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
            "effect_authority": effect_authority,
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
    effect_authority: dict[str, Any] | None = None,
    security_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scope = _trusted_agent_scope(security_context)
    console_body: dict[str, Any] = {
        "security_context": security_context,
        "wisdom_bit_id": wisdom_bit_id,
        "cartridge_id": cartridge_id,
        "payload": payload or {},
    }
    if effect_authority is not None:
        console_body["effect_authority"] = effect_authority
    try:
        result = await _call_console_under_fence(
            scope,
            effect_authority,
            "/internal/intelligence/wisdom-bits/run",
            console_body,
            timeout=45.0,
        )
    except HTTPException as exc:
        if exc.status_code != 422 or "effect_authority" not in console_body:
            raise
        console_body.pop("effect_authority")
        result = await _call_console_under_fence(
            scope,
            effect_authority,
            "/internal/intelligence/wisdom-bits/run",
            console_body,
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
            "severity": {
                "type": "string",
                "enum": ["low", "medium", "high", "critical"],
            },
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
    effect_authority: dict[str, Any] | None = None,
    security_context: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    unexpected = sorted(extra)
    if unexpected:
        raise HTTPException(400, f"unsupported alert args: {', '.join(unexpected)}")
    return _raise_alert_impl(
        alert_type=alert_type,
        cartridge_id=cartridge_id,
        domain=domain,
        source_dataset=source_dataset,
        entity_key=entity_key,
        title=title,
        message=message,
        severity=severity,
        confidence=confidence,
        entity_label=entity_label,
        recommendation=recommendation,
        impact_estimate=impact_estimate,
        impact_currency=impact_currency,
        evidence_refs=evidence_refs,
        hypothesis=hypothesis,
        expected_outcome=expected_outcome,
        effect_authority=effect_authority,
        security_context=security_context,
    )


def _raise_alert_impl(
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
    effect_authority: dict[str, Any] | None = None,
    security_context: dict[str, Any] | None = None,
    _server_metadata_patch: dict[str, Any] | None = None,
    _server_event_kind: str | None = None,
    _server_event_metadata: dict[str, Any] | None = None,
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
    entity_label = _as_text(
        entity_label, "entity_label", max_len=_MAX_SHORT_TEXT, required=False
    )
    title = _as_text(title, "title", max_len=_MAX_SHORT_TEXT)
    message = _as_text(message, "message", max_len=_MAX_TEXT)
    severity = _as_severity(severity)
    confidence = _as_confidence(confidence)
    recommendation = _as_text(
        recommendation, "recommendation", max_len=_MAX_TEXT, required=False
    )
    hypothesis = _as_text(hypothesis, "hypothesis", max_len=_MAX_TEXT, required=False)
    expected_outcome = _as_text(
        expected_outcome, "expected_outcome", max_len=_MAX_TEXT, required=False
    )
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
        "recommendation": recommendation
        or "Revisar evidencia y decidir accion supervisada.",
        "hypothesis": hypothesis,
        "expected_outcome": expected_outcome,
        "evidence_refs": evidence,
        "execution_status": "not_started",
        "control_state": {"source": "agent", "advisory": True},
    }
    if _server_metadata_patch:
        metadata.update(_server_metadata_patch)

    with _conn() as conn, conn.cursor() as cur:
        _set_rls_scope(cur, scope["tenant_id"], scope["workspace_id"])
        if effect_authority is not None:
            _lock_scheduled_effect(cur, scope, effect_authority)
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
        previous_metadata = (
            existing[1] if existing and isinstance(existing[1], dict) else {}
        )
        occurrence_count = int(previous_metadata.get("occurrence_count") or 0) + 1
        metadata["occurrence_count"] = occurrence_count
        deduped = existing is not None
        terminal = bool(existing and str(existing[0]) in _TERMINAL_STATUSES)
        event_prefix = _server_event_kind or "agent_alert"
        event_type = f"{event_prefix}_{'deduped' if deduped else 'created'}"

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
                Json(
                    {
                        "source": "agent",
                        "advisory": True,
                        "agent_id": scope["agent_id"],
                        "agent_run_id": scope["agent_run_id"],
                        "deduped": deduped,
                        "terminal_preserved": terminal,
                        "occurrence_count": occurrence_count,
                        **(_server_event_metadata or {}),
                    }
                ),
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
            "severity": {
                "type": "string",
                "enum": ["low", "medium", "high", "critical"],
            },
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
    effect_authority: dict[str, Any] | None = None,
    security_context: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    unexpected = sorted(set(extra) | (set(extra) & _FORBIDDEN_ARGS))
    if unexpected:
        raise HTTPException(
            400, f"unsupported analysis alert args: {', '.join(unexpected)}"
        )
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
    augmented_refs.append(
        {
            "kind": "analysis_evidence",
            "engine": analysis["engine"],
            "engine_run_id": analysis["engine_run_id"],
        }
    )
    metadata_patch = {
        "origin": analysis["engine"],
        "analysis_type": analysis["analysis_type"],
        "analysis_evidence": analysis,
        "engine_run_id": analysis["engine_run_id"],
    }
    result = _raise_alert_impl(
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
        effect_authority=effect_authority,
        security_context=security_context,
        _server_metadata_patch=metadata_patch,
        _server_event_kind="agent_analysis_alert",
        _server_event_metadata={
            "engine": analysis["engine"],
            "engine_run_id": analysis["engine_run_id"],
        },
    )

    return {
        **result,
        "event_type": (
            "agent_analysis_alert_deduped"
            if result.get("deduped")
            else "agent_analysis_alert_created"
        ),
        "engine": analysis["engine"],
        "engine_run_id": analysis["engine_run_id"],
        "analysis_evidence": analysis,
    }
