from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Iterable

import httpx
from fastapi import HTTPException

from app.security import get_internal_api_key
from app.services import audit_service, auth
from app.services.security_context import build_security_context, rls_user_context


REFINEMENT_URL = os.environ.get("REFINEMENT_URL", "http://refinement:8500").rstrip("/")

ACTIVE_INSTALLATION_STATUSES = {"ready", "active"}
TERMINAL_ITEM_STATUSES = {"approved", "dismissed", "resolved"}
ITEM_STATUSES = {"open", "in_review", "decision_created", "approved", "dismissed", "resolved"}
SEVERITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}
OMEGA_STEPS = [
    {"id": "signals", "label": "Senales"},
    {"id": "investigation", "label": "Investigacion"},
    {"id": "options", "label": "Opciones"},
    {"id": "decision", "label": "Decision"},
    {"id": "execution", "label": "Ejecucion"},
    {"id": "control", "label": "Control"},
    {"id": "lessons", "label": "Lecciones aprendidas"},
]

EXECUTION_STATUSES = {
    "not_started",
    "preview_generated",
    "dry_run_validated",
    "blocked",
    "failed",
    "executed",
}

ACTION_TEMPLATES: dict[str, dict[str, Any]] = {
    "restore_data_source": {
        "template_id": "restore_data_source",
        "cartridge_id": "platform",
        "label": "Restaurar fuente de datos",
        "description": "Valida instalacion, credenciales, materializacion y scope tenant/workspace.",
        "action_kind": "data_recovery",
        "risk_level": "medium",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
    "create_followup_task": {
        "template_id": "create_followup_task",
        "cartridge_id": "platform",
        "label": "Crear seguimiento operativo",
        "description": "Genera una tarea auditada para responsable operativo.",
        "action_kind": "followup_task",
        "risk_level": "low",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
    "request_owner_review": {
        "template_id": "request_owner_review",
        "cartridge_id": "platform",
        "label": "Solicitar revision de owner",
        "description": "Prepara solicitud de revision humana con evidencia y SQL.",
        "action_kind": "owner_review",
        "risk_level": "low",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
    "prepare_replicon_adjustment": {
        "template_id": "prepare_replicon_adjustment",
        "cartridge_id": "replicon",
        "label": "Preparar ajuste Replicon",
        "description": "Construye payload seguro para revisar billing, timesheet o asignacion en Replicon.",
        "action_kind": "replicon_adjustment",
        "risk_level": "high",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
    "prepare_billing_review": {
        "template_id": "prepare_billing_review",
        "cartridge_id": "replicon",
        "label": "Preparar revision de facturacion",
        "description": "Construye evidencia para validar WIP, margen, horas y facturacion.",
        "action_kind": "billing_review",
        "risk_level": "medium",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
    "prepare_sap_review": {
        "template_id": "prepare_sap_review",
        "cartridge_id": "sap_s4hana",
        "label": "Preparar revision SAP",
        "description": "Construye payload de revision para revenue, backlog, compras o maestro S/4.",
        "action_kind": "sap_review",
        "risk_level": "high",
        "mode_default": "dry_run",
        "requires_approval": True,
    },
}


@dataclass(frozen=True)
class ControlRoomSource:
    dataset: str
    cartridge: str
    domain: str
    module_label: str
    entity_kind: str
    entity_id_field: str
    entity_label_field: str
    kind: str = "anomaly"
    normalizer: str = "standard_anomaly"


@dataclass(frozen=True)
class ControlRoomModule:
    cartridge: str
    label: str
    domain: str
    accent: str
    sources: tuple[ControlRoomSource, ...] = ()
    operational: bool = False


MODULES: tuple[ControlRoomModule, ...] = (
    ControlRoomModule(
        cartridge="sap_hcm",
        label="Personal",
        domain="Recursos Humanos",
        accent="#7c3aed",
        sources=(
            ControlRoomSource(
                dataset="employees_anomalies",
                cartridge="sap_hcm",
                domain="Recursos Humanos",
                module_label="Personal",
                entity_kind="Empleado",
                entity_id_field="pernr",
                entity_label_field="full_name",
            ),
        ),
    ),
    ControlRoomModule(
        cartridge="sap_successfactors",
        label="Employee Central",
        domain="Recursos Humanos",
        accent="#8b5cf6",
        sources=(
            ControlRoomSource(
                dataset="sap_successfactors_employees_anomalies",
                cartridge="sap_successfactors",
                domain="Recursos Humanos",
                module_label="Employee Central",
                entity_kind="Empleado",
                entity_id_field="user_id",
                entity_label_field="full_name",
            ),
        ),
    ),
    ControlRoomModule(
        cartridge="sap_s4hana",
        label="ERP Core",
        domain="Finanzas",
        accent="#10b981",
        sources=(
            ControlRoomSource(
                dataset="business_partner_anomalies",
                cartridge="sap_s4hana",
                domain="Finanzas",
                module_label="ERP Core",
                entity_kind="Business Partner",
                entity_id_field="business_partner",
                entity_label_field="full_name",
            ),
            ControlRoomSource(
                dataset="revenue_by_customer",
                cartridge="sap_s4hana",
                domain="Ventas",
                module_label="Ventas",
                entity_kind="Cliente",
                entity_id_field="customer_code",
                entity_label_field="customer_code",
                kind="control_item",
                normalizer="s4_revenue",
            ),
            ControlRoomSource(
                dataset="open_sales_orders",
                cartridge="sap_s4hana",
                domain="Ventas",
                module_label="Ventas",
                entity_kind="Cliente",
                entity_id_field="customer_code",
                entity_label_field="customer_code",
                kind="control_item",
                normalizer="s4_backlog",
            ),
            ControlRoomSource(
                dataset="purchase_spend_by_supplier",
                cartridge="sap_s4hana",
                domain="Compras",
                module_label="Compras",
                entity_kind="Proveedor",
                entity_id_field="supplier_code",
                entity_label_field="supplier_code",
                kind="control_item",
                normalizer="s4_supplier_spend",
            ),
        ),
    ),
    ControlRoomModule(
        cartridge="replicon",
        label="Servicios Profesionales",
        domain="Operacion",
        accent="#0ea5e9",
        sources=(
            ControlRoomSource(
                dataset="consultor_asignacion",
                cartridge="replicon",
                domain="Operacion",
                module_label="Asignacion",
                entity_kind="Consultor",
                entity_id_field="consultor",
                entity_label_field="consultor",
                kind="control_item",
                normalizer="replicon_allocation",
            ),
            ControlRoomSource(
                dataset="consultor_timesheet_semanal",
                cartridge="replicon",
                domain="Operacion",
                module_label="Timesheets",
                entity_kind="Consultor",
                entity_id_field="consultor",
                entity_label_field="consultor",
                kind="control_item",
                normalizer="replicon_timesheet",
            ),
            ControlRoomSource(
                dataset="pnl_mensual",
                cartridge="replicon",
                domain="Finanzas",
                module_label="P&L",
                entity_kind="Proyecto",
                entity_id_field="proyecto",
                entity_label_field="project_name",
                kind="control_item",
                normalizer="replicon_pnl",
            ),
            ControlRoomSource(
                dataset="analytic_skill_gap_by_manager",
                cartridge="replicon",
                domain="Recursos Humanos",
                module_label="Skills",
                entity_kind="Manager",
                entity_id_field="manager_name",
                entity_label_field="manager_name",
                kind="control_item",
                normalizer="replicon_skill_gap",
            ),
        ),
    ),
    ControlRoomModule(
        cartridge="platform",
        label="Plataforma",
        domain="Operacion",
        accent="#64748b",
        operational=True,
    ),
)

DOMAIN_ORDER = ["Recursos Humanos", "Nomina", "Finanzas", "Presupuestos", "Compras", "Ventas", "Operacion"]
DOMAIN_ACCENTS = {
    "Recursos Humanos": "#7c3aed",
    "Nomina": "#ef4444",
    "Finanzas": "#10b981",
    "Presupuestos": "#0891b2",
    "Compras": "#f59e0b",
    "Ventas": "#8b5cf6",
    "Operacion": "#64748b",
}

ANOMALY_COPY: dict[str, dict[str, str]] = {
    "missing_cost_center": {
        "title": "Empleado activo sin centro de costo",
        "recommendation": "Asignar centro de costo, validar owner financiero y registrar seguimiento en una decision compartida.",
        "root_cause": "Dato maestro incompleto en la estructura organizacional.",
    },
    "missing_position": {
        "title": "Empleado activo sin posicion",
        "recommendation": "Corregir asignacion organizacional y revisar que la posicion exista antes del siguiente corte operativo.",
        "root_cause": "La asignacion de posicion no llego completa desde el origen.",
    },
    "terminated_but_active": {
        "title": "Empleado dado de baja sigue activo",
        "recommendation": "Validar ultima accion de personal, bloquear accesos si aplica y abrir remediacion con responsable de HR Ops.",
        "root_cause": "Baja laboral no sincronizada con estado operativo activo.",
    },
    "missing_address": {
        "title": "Business Partner sin direccion",
        "recommendation": "Completar datos maestros del partner antes de nuevas ordenes, facturacion o analisis de credito.",
        "root_cause": "Maestro financiero incompleto para operaciones comerciales.",
    },
    "duplicate_name": {
        "title": "Business Partners con nombre duplicado",
        "recommendation": "Revisar duplicidad de maestro, consolidar registros o marcar excepcion aprobada.",
        "root_cause": "Posible alta duplicada o normalizacion insuficiente del nombre legal.",
    },
    "missing_department": {
        "title": "Empleado sin departamento",
        "recommendation": "Asignar departamento valido y verificar reglas de reporting para evitar analisis incompleto.",
        "root_cause": "Employee Central no trae unidad organizacional completa.",
    },
    "missing_manager": {
        "title": "Empleado sin manager",
        "recommendation": "Confirmar si es excepcion ejecutiva; si no lo es, asignar manager y registrar control de seguimiento.",
        "root_cause": "La cadena de mando esta incompleta o no fue replicada.",
    },
    "invalid_job_code": {
        "title": "Job code invalido",
        "recommendation": "Corregir job code contra el catalogo FOJobCode y revisar impactos en compensacion/reporting.",
        "root_cause": "Codigo de puesto no existe o no esta vigente en el catalogo de referencia.",
    },
}


DatasetFetcher = Callable[[str, dict | None, int], Awaitable[list[dict[str, Any]]]]


def _is_production_env() -> bool:
    return os.environ.get("APP_ENV", "production").strip().lower() in {"production", "prod"}


def _internal_headers(server: str) -> dict[str, str]:
    pair = os.environ.get(f"INTERNAL_API_KEY_CONSOLE_TO_{server}")
    if pair:
        key = pair
    elif _is_production_env():
        raise RuntimeError(f"Missing INTERNAL_API_KEY_CONSOLE_TO_{server}; legacy fallback disabled in production")
    else:
        key = get_internal_api_key()
    return {"x-api-key": key, "x-internal-service": "console"}


def _mcp_payload(tool: str, args: dict[str, Any], user: dict | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"tool": tool, "args": args}
    if user is not None:
        payload["security_context"] = build_security_context(user)
    return payload


async def query_dataset_rows(dataset: str, user: dict | None, limit: int = 1000) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(headers=_internal_headers("REFINEMENT"), timeout=45) as client:
        response = await client.post(
            f"{REFINEMENT_URL}/mcp/invoke",
            json=_mcp_payload(
                "query_dataset",
                {"name": dataset, "limit": limit, "user_context": rls_user_context(user)},
                user,
            ),
        )
    if response.status_code >= 400:
        raise HTTPException(response.status_code, f"dataset unavailable: {dataset}")
    payload = response.json()
    if isinstance(payload, dict) and payload.get("error") and "data" not in payload:
        detail = payload.get("error") or payload.get("code") or f"dataset unavailable: {dataset}"
        raise HTTPException(503, str(detail))
    data = payload.get("data", payload.get("result", payload))
    if isinstance(data, dict):
        if data.get("error") and "data" not in data:
            detail = data.get("error") or data.get("code") or f"dataset unavailable: {dataset}"
            raise HTTPException(503, str(detail))
        data = data.get("data", [])
    if not isinstance(data, list):
        return []
    return [dict(row) for row in data if isinstance(row, dict)]


def _details(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"raw": value}
        except json.JSONDecodeError:
            return {"raw": value}
    return {}


def _encode_id(parts: dict[str, Any]) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _severity(value: Any) -> str:
    sev = str(value or "medium").strip().lower()
    if sev in {"critica", "critical", "critico"}:
        return "critical"
    if sev in {"alta", "high"}:
        return "high"
    if sev in {"media", "moderada", "medium"}:
        return "medium"
    if sev in {"baja", "low", "ok", "satisfactoria"}:
        return "low"
    return "medium"


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _copy_for(anomaly_type: str) -> dict[str, str]:
    return ANOMALY_COPY.get(
        anomaly_type,
        {
            "title": anomaly_type.replace("_", " ").strip().title() or "Senal operativa",
            "recommendation": "Revisar el registro, asignar responsable y documentar la decision tomada.",
            "root_cause": "La fuente genero una senal fuera de regla que requiere investigacion.",
        },
    )


def _workspace_scope(user: dict | None) -> tuple[str | None, str]:
    workspace_id = (user or {}).get("active_workspace_id") or (user or {}).get("workspace_id")
    tenant_id = (user or {}).get("active_tenant_id") or (user or {}).get("tenant_id")
    if not workspace_id:
        raise HTTPException(400, "active workspace is required")
    return (str(tenant_id) if tenant_id else None), str(workspace_id)


def _workspace_id(user: dict | None) -> str:
    return _workspace_scope(user)[1]


def _module_by_cartridge() -> dict[str, ControlRoomModule]:
    return {module.cartridge: module for module in MODULES}


def _all_sources() -> tuple[ControlRoomSource, ...]:
    sources: list[ControlRoomSource] = []
    for module in MODULES:
        sources.extend(module.sources)
    return tuple(sources)


def _allowed_from_user(user: dict | None) -> set[str] | None:
    ctx = build_security_context(user)
    allowed = {
        str(item).strip()
        for item in (ctx.get("allowed_cartridges") or [])
        if str(item).strip()
    }
    if "*" in allowed:
        return None
    return allowed if allowed else None


def _row_to_public(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key, value in list(data.items()):
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
    return data


async def _installed_cartridges(user: dict | None) -> list[dict[str, Any]]:
    tenant_id, workspace_id = _workspace_scope(user)
    pool = await auth.pool()
    try:
        rows = await pool.fetch(
            """
            SELECT
                ci.cartridge_id,
                ci.status AS installation_status,
                ci.current_step,
                ci.error_message,
                ci.ready_at,
                COALESCE(mp.name, c.name, ci.cartridge_id) AS label,
                COALESCE(c.category, 'cartridge') AS category
            FROM cartridge_installations ci
            LEFT JOIN cartridges c ON c.id = ci.cartridge_id
            LEFT JOIN marketplace_products mp ON mp.cartridge_id = ci.cartridge_id
            WHERE ci.tenant_id = $1
              AND ci.workspace_id = $2
              AND ci.status = ANY($3::text[])
              AND NOT EXISTS (
                  SELECT 1
                    FROM user_cartridge_overrides uco
                   WHERE uco.tenant_id = ci.tenant_id
                     AND uco.workspace_id = ci.workspace_id
                     AND uco.cartridge_id = ci.cartridge_id
                     AND uco.user_id = $4
                     AND uco.mode = 'deny'
              )
            ORDER BY lower(COALESCE(mp.name, c.name, ci.cartridge_id))
            """,
            tenant_id,
            workspace_id,
            sorted(ACTIVE_INSTALLATION_STATUSES),
            (user or {}).get("id"),
        )
        return [_row_to_public(row) for row in rows]
    except Exception:
        allowed = _allowed_from_user(user)
        modules = MODULES if allowed is None else [module for module in MODULES if module.cartridge in allowed]
        return [
            {
                "cartridge_id": module.cartridge,
                "installation_status": "ready",
                "current_step": "fallback",
                "label": module.label,
                "category": "platform" if module.operational else "cartridge",
            }
            for module in modules
        ]


async def _fetch_source(
    source: ControlRoomSource,
    user: dict | None,
    fetcher: DatasetFetcher,
    limit_per_source: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        rows = await fetcher(source.dataset, user, limit_per_source)
    except HTTPException as exc:
        status = "missing" if exc.status_code == 404 else "unavailable"
        return [], {
            "dataset": source.dataset,
            "cartridge": source.cartridge,
            "domain": source.domain,
            "module": source.module_label,
            "status": status,
            "error": str(exc.detail),
            "count": 0,
        }
    except Exception as exc:
        return [], {
            "dataset": source.dataset,
            "cartridge": source.cartridge,
            "domain": source.domain,
            "module": source.module_label,
            "status": "unavailable",
            "error": str(exc),
            "count": 0,
        }

    if rows and source.normalizer == "standard_anomaly":
        missing_contract = all(
            source.entity_id_field not in row and source.entity_label_field not in row
            for row in rows
        )
        if missing_contract:
            return [], {
                "dataset": source.dataset,
                "cartridge": source.cartridge,
                "domain": source.domain,
                "module": source.module_label,
                "status": "invalid_schema",
                "error": f"missing expected fields: {source.entity_id_field}/{source.entity_label_field}",
                "count": len(rows),
            }
    return rows, {
        "dataset": source.dataset,
        "cartridge": source.cartridge,
        "domain": source.domain,
        "module": source.module_label,
        "status": "empty" if not rows else "ok",
        "count": len(rows),
    }


def _base_item(source: ControlRoomSource, row: dict[str, Any], item_type: str, entity_id: str, label: str) -> dict[str, Any]:
    severity = _severity(row.get("severity"))
    details = _details(row.get("details"))
    detected_at = str(row.get("detected_at") or row.get("mes") or row.get("semana") or "")
    technical_sql = (
        f"SELECT * FROM {source.dataset} "
        f"WHERE {source.entity_id_field} = '{str(entity_id or label).replace("'", "''")}' "
        f"LIMIT 50"
    )
    return {
        "id": _encode_id({
            "dataset": source.dataset,
            "type": item_type,
            "entity": entity_id or label,
        }),
        "kind": source.kind,
        "domain": source.domain,
        "module": source.module_label,
        "cartridge": source.cartridge,
        "source_dataset": source.dataset,
        "entity_kind": source.entity_kind,
        "entity_id": entity_id,
        "entity_label": label,
        "anomaly_type": item_type,
        "severity": severity,
        "severity_weight": SEVERITY_WEIGHT[severity],
        "detected_at": detected_at,
        "details": details,
        "sql": technical_sql,
        "status": "open",
        "decision_id": None,
    }


def _normalize_standard_anomaly(source: ControlRoomSource, row: dict[str, Any]) -> dict[str, Any]:
    anomaly_type = str(row.get("anomaly_type") or "unknown").strip()
    entity_id = str(row.get(source.entity_id_field) or "").strip()
    label = str(row.get(source.entity_label_field) or entity_id or "Sin nombre").strip()
    details = _details(row.get("details"))
    copy = _copy_for(anomaly_type)
    item = _base_item(source, row, anomaly_type, entity_id, label)
    reason = str(details.get("reason") or "").strip()
    item.update({
        "title": copy["title"],
        "description": reason or f"{source.entity_kind} {label} requiere revision operativa.",
        "recommendation": copy["recommendation"],
        "root_cause": str(details.get("root_cause") or copy["root_cause"]),
        "impact": str(details.get("impact") or f"Riesgo operativo en {source.domain}."),
    })
    return item


def _normalize_replicon_allocation(source: ControlRoomSource, row: dict[str, Any]) -> dict[str, Any] | None:
    pct = _num(row.get("pct_asignacion"))
    if pct is None or 40 <= pct <= 110:
        return None
    consultor = str(row.get("consultor") or "Sin consultor").strip()
    proyecto = str(row.get("proyecto") or row.get("project_name") or "Sin proyecto").strip()
    item_type = "over_allocation" if pct > 110 else "under_allocation"
    severity = "high" if pct > 130 or pct < 20 else "medium"
    item = _base_item(source, {**row, "severity": severity}, item_type, f"{consultor}:{proyecto}", consultor)
    direction = "sobreasignacion" if pct > 110 else "subasignacion"
    item.update({
        "title": f"{consultor} con {direction} operativa",
        "description": f"{consultor} registra {pct:.1f}% de asignacion en {proyecto}.",
        "recommendation": "Rebalancear carga con el revenue manager y documentar excepcion si la asignacion es temporal.",
        "root_cause": "Diferencia entre capacidad mensual esperada y horas asignadas.",
        "impact": "Riesgo de capacidad, margen o entrega del proyecto.",
        "details": {**item["details"], **row},
    })
    return item


def _normalize_replicon_timesheet(source: ControlRoomSource, row: dict[str, Any]) -> dict[str, Any] | None:
    total = _num(row.get("horas_total")) or 0
    no_billable = _num(row.get("horas_no_facturables")) or 0
    if total <= 0:
        return None
    ratio = no_billable / total
    if ratio < 0.35:
        return None
    consultor = str(row.get("consultor") or "Sin consultor").strip()
    proyecto = str(row.get("proyecto") or row.get("project_name") or "Sin proyecto").strip()
    severity = "high" if ratio >= 0.55 else "medium"
    item = _base_item(source, {**row, "severity": severity}, "non_billable_ratio", f"{consultor}:{proyecto}", consultor)
    item.update({
        "title": "Horas no facturables fuera de rango",
        "description": f"{consultor} tiene {ratio:.0%} de horas no facturables en {proyecto}.",
        "recommendation": "Validar causa con PM/RM, reclasificar si procede y ajustar forecast de margen.",
        "root_cause": "Registro de tiempo no facturable alto frente al total semanal.",
        "impact": "Puede erosionar margen y ocultar demanda no planificada.",
        "details": {**item["details"], **row},
    })
    return item


def _normalize_replicon_pnl(source: ControlRoomSource, row: dict[str, Any]) -> dict[str, Any] | None:
    margin = _num(row.get("margen_bruto_pct"))
    wip = _num(row.get("wip_usd")) or 0
    if (margin is None or margin >= 20) and abs(wip) < 5000:
        return None
    proyecto = str(row.get("proyecto") or row.get("project_name") or "Sin proyecto").strip()
    manager = str(row.get("revenue_manager") or "Sin RM").strip()
    if margin is not None and margin < 0:
        severity = "critical"
    elif margin is not None and margin < 20:
        severity = "high"
    else:
        severity = "medium"
    item_type = "low_margin" if margin is not None and margin < 20 else "wip_variance"
    item = _base_item(source, {**row, "severity": severity}, item_type, proyecto, str(row.get("project_name") or proyecto))
    item.update({
        "title": "Proyecto con margen o WIP fuera de control",
        "description": f"{proyecto} esta bajo {manager}; margen={margin if margin is not None else 'N/D'}%, WIP={wip:,.0f} USD.",
        "recommendation": "Revisar revenue, facturacion, costo hundido y compromiso de remediacion con finanzas.",
        "root_cause": "Desviacion entre ingreso reconocido, facturacion y costo total.",
        "impact": "Riesgo financiero directo en margen, cash flow o forecast.",
        "details": {**item["details"], **row},
    })
    return item


def _normalize_replicon_skill_gap(source: ControlRoomSource, row: dict[str, Any]) -> dict[str, Any] | None:
    level = str(row.get("brecha_nivel") or "").strip().upper()
    if level not in {"CRITICA", "MODERADA"}:
        return None
    manager = str(row.get("manager_name") or "Sin manager").strip()
    category = str(row.get("skill_category") or "Sin categoria").strip()
    severity = "high" if level == "CRITICA" else "medium"
    item = _base_item(source, {**row, "severity": severity}, "skill_gap", f"{manager}:{category}", manager)
    item.update({
        "title": "Brecha de skills en equipo",
        "description": f"{manager} tiene brecha {level.lower()} en {category}.",
        "recommendation": "Priorizar capacitacion, mentorias o staffing alterno antes de nuevas asignaciones criticas.",
        "root_cause": "Cobertura de habilidades insuficiente para la demanda operativa.",
        "impact": "Riesgo de delivery, calidad o dependencia de pocos expertos.",
        "details": {**item["details"], **row},
    })
    return item


def _normalize_s4_revenue(source: ControlRoomSource, row: dict[str, Any]) -> dict[str, Any] | None:
    revenue = _num(row.get("revenue"))
    if revenue is None or revenue >= 0:
        return None
    customer = str(row.get("customer_code") or "Sin cliente").strip()
    month = str(row.get("revenue_month") or row.get("mes") or "").strip()
    item = _base_item(
        source,
        {**row, "severity": "high"},
        "negative_revenue",
        f"{customer}:{month}",
        customer,
    )
    item.update({
        "title": "Revenue negativo en ventas",
        "description": f"{customer} registra revenue negativo por {abs(revenue):,.0f} en {month or 'el periodo'}.",
        "recommendation": "Revisar notas de credito, anulaciones y conciliacion de facturacion antes del cierre.",
        "root_cause": "La facturacion neta del periodo quedo por debajo de cero.",
        "impact": "Riesgo de distorsion de revenue, forecast y margen comercial.",
        "details": {**item["details"], **row},
    })
    return item


def _normalize_s4_backlog(source: ControlRoomSource, row: dict[str, Any]) -> dict[str, Any] | None:
    age = _num(row.get("oldest_age_days")) or 0
    open_value = _num(row.get("open_value")) or 0
    if age < 45 and open_value < 50000:
        return None
    customer = str(row.get("customer_code") or "Sin cliente").strip()
    severity = "critical" if age >= 90 or open_value >= 250000 else "high"
    item = _base_item(
        source,
        {**row, "severity": severity},
        "aged_sales_backlog",
        customer,
        customer,
    )
    item.update({
        "title": "Backlog comercial envejecido",
        "description": f"{customer} tiene pedidos abiertos por {open_value:,.0f} con antiguedad maxima de {age:.0f} dias.",
        "recommendation": "Validar bloqueo, entrega, facturacion pendiente y responsable comercial.",
        "root_cause": "Pedidos abiertos permanecen sin completar fuera del ciclo normal.",
        "impact": "Riesgo de cash flow, cumplimiento de entrega y forecast de ventas.",
        "details": {**item["details"], **row},
    })
    return item


def _normalize_s4_supplier_spend(source: ControlRoomSource, row: dict[str, Any]) -> dict[str, Any] | None:
    spend = _num(row.get("total_spend")) or 0
    if spend < 250000:
        return None
    supplier = str(row.get("supplier_code") or "Sin proveedor").strip()
    month = str(row.get("spend_month") or "").strip()
    severity = "high" if spend >= 750000 else "medium"
    item = _base_item(
        source,
        {**row, "severity": severity},
        "supplier_spend_concentration",
        f"{supplier}:{month}",
        supplier,
    )
    item.update({
        "title": "Gasto alto concentrado en proveedor",
        "description": f"{supplier} concentra {spend:,.0f} de gasto en {month or 'el periodo'}.",
        "recommendation": "Revisar aprobaciones, categoria, contrato vigente y comparativo contra presupuesto.",
        "root_cause": "Concentracion de gasto relevante en compras del periodo.",
        "impact": "Riesgo de sobrepresupuesto, dependencia de proveedor o control de aprobaciones.",
        "details": {**item["details"], **row},
    })
    return item


def _normalize_row(source: ControlRoomSource, row: dict[str, Any]) -> dict[str, Any] | None:
    if source.normalizer == "standard_anomaly":
        return _normalize_standard_anomaly(source, row)
    if source.normalizer == "replicon_allocation":
        return _normalize_replicon_allocation(source, row)
    if source.normalizer == "replicon_timesheet":
        return _normalize_replicon_timesheet(source, row)
    if source.normalizer == "replicon_pnl":
        return _normalize_replicon_pnl(source, row)
    if source.normalizer == "replicon_skill_gap":
        return _normalize_replicon_skill_gap(source, row)
    if source.normalizer == "s4_revenue":
        return _normalize_s4_revenue(source, row)
    if source.normalizer == "s4_backlog":
        return _normalize_s4_backlog(source, row)
    if source.normalizer == "s4_supplier_spend":
        return _normalize_s4_supplier_spend(source, row)
    return None


def _money_sum(rows: Iterable[dict[str, Any]], field: str) -> float:
    return round(sum(_num(row.get(field)) or 0 for row in rows), 2)


def _ratio(numerator: float, denominator: float) -> float | None:
    if not denominator:
        return None
    return round((numerator / denominator) * 100, 2)


def _financial_metrics(
    sources: list[dict[str, Any]],
    rows_by_dataset: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    pnl_rows = rows_by_dataset.get("pnl_mensual", [])
    revenue_rows = rows_by_dataset.get("revenue_by_customer", [])
    backlog_rows = rows_by_dataset.get("open_sales_orders", [])
    purchase_rows = rows_by_dataset.get("purchase_spend_by_supplier", [])
    relevant = {
        source["dataset"]: {
            "status": source["status"],
            "count": int(source.get("count") or 0),
            "domain": source.get("domain"),
            "cartridge": source.get("cartridge"),
        }
        for source in sources
        if source["dataset"] in {
            "pnl_mensual",
            "revenue_by_customer",
            "open_sales_orders",
            "purchase_spend_by_supplier",
        }
    }

    revenue_usd = _money_sum(pnl_rows, "revenue_usd")
    billed_usd = _money_sum(pnl_rows, "facturacion_mes_usd")
    wip_usd = _money_sum(pnl_rows, "wip_usd")
    cost_usd = _money_sum(pnl_rows, "costo_total")
    margin_usd = _money_sum(pnl_rows, "margen_bruto_usd")
    sales_revenue = _money_sum(revenue_rows, "revenue")
    backlog_value = _money_sum(backlog_rows, "open_value")
    purchase_spend = _money_sum(purchase_rows, "total_spend")
    open_orders = int(_money_sum(backlog_rows, "open_orders"))
    oldest_backlog_days = int(max((_num(row.get("oldest_age_days")) or 0 for row in backlog_rows), default=0))

    risk_rows: list[dict[str, Any]] = []
    for row in pnl_rows:
        margin_pct = _num(row.get("margen_bruto_pct"))
        wip = _num(row.get("wip_usd")) or 0
        if (margin_pct is not None and margin_pct < 20) or abs(wip) >= 5000:
            risk_rows.append({
                "label": str(row.get("project_name") or row.get("proyecto") or "Proyecto"),
                "owner": str(row.get("revenue_manager") or "N/D"),
                "margin_pct": margin_pct,
                "wip_usd": round(wip, 2),
                "margin_usd": round(_num(row.get("margen_bruto_usd")) or 0, 2),
            })
    risk_rows.sort(key=lambda row: (row["margin_pct"] if row["margin_pct"] is not None else 999, -abs(row["wip_usd"])))

    available = any(source["status"] == "ok" and source.get("count", 0) for source in relevant.values())
    if available:
        status = "ok"
    elif any(source["status"] in {"unavailable", "invalid_schema"} for source in relevant.values()):
        status = "unavailable"
    elif any(source["status"] == "missing" for source in relevant.values()):
        status = "missing"
    elif relevant:
        status = "empty"
    else:
        status = "not_installed"

    return {
        "status": status,
        "sources": relevant,
        "revenue_usd": revenue_usd,
        "billed_usd": billed_usd,
        "wip_usd": wip_usd,
        "cost_usd": cost_usd,
        "margin_usd": margin_usd,
        "margin_pct": _ratio(margin_usd, revenue_usd),
        "sales_revenue": sales_revenue,
        "backlog_value": backlog_value,
        "open_orders": open_orders,
        "oldest_backlog_days": oldest_backlog_days,
        "purchase_spend": purchase_spend,
        "risk_projects": risk_rows[:6],
    }


def _cycle_counts(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {step["id"]: 0 for step in OMEGA_STEPS}
    for item in items:
        status = str(item.get("status") or "open")
        counts["signals"] += 1
        if status in {"open", "in_review"}:
            counts["investigation"] += 1
        if status == "in_review" or item.get("selected_option_id"):
            counts["options"] += 1
        if item.get("decision_id") or status in {"decision_created", "approved", "resolved"}:
            counts["decision"] += 1
        if status in {"approved", "resolved"}:
            counts["execution"] += 1
            counts["control"] += 1
            counts["lessons"] += 1
        elif status == "dismissed":
            counts["control"] += 1
    return counts


def _lessons_for_item(item: dict[str, Any]) -> list[str]:
    existing = item.get("lessons")
    if isinstance(existing, list):
        cleaned = [str(rule).strip() for rule in existing if str(rule).strip()]
        if cleaned:
            return cleaned
    return [
        f"Si {item.get('source_dataset')} genera {item.get('anomaly_type')}, abrir revision OMEGA.",
        "Toda aprobacion debe quedar ligada a decision_actions y audit_events.",
    ]


def _external_writeback_enabled() -> bool:
    return os.environ.get("CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _impact_payload(
    *,
    item: dict[str, Any],
    estimate: float | None,
    status: str,
    confidence: float,
    drivers: list[dict[str, Any]],
    formula: str,
    explanation: str,
    currency: str = "USD",
) -> dict[str, Any]:
    estimate_value = round(float(estimate or 0), 2) if estimate is not None else None
    severity_weight = SEVERITY_WEIGHT.get(str(item.get("severity") or "medium"), 2)
    impact_points = 0 if estimate_value is None else min(42, int(abs(estimate_value) / 10_000))
    priority_score = min(100, max(0, severity_weight * 14 + impact_points + int(confidence * 20)))
    return {
        "item_id": item.get("id"),
        "status": status,
        "estimate": estimate_value,
        "currency": currency,
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "priority_score": priority_score,
        "drivers": drivers,
        "formula": formula,
        "explanation": explanation,
    }


def _impact_for_item(item: dict[str, Any]) -> dict[str, Any]:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    if item.get("kind") == "source_state":
        return _impact_payload(
            item=item,
            estimate=None,
            status="unavailable",
            confidence=0.2,
            drivers=[{"label": "Estado fuente", "value": details.get("source_status") or item.get("status")}],
            formula="Sin impacto monetario hasta restaurar materializacion.",
            explanation="La fuente no entrega datos suficientes para calcular dinero sin inventar cifras.",
        )

    stored = _num(item.get("impact_estimate"))
    if stored is not None and stored > 0:
        return _impact_payload(
            item=item,
            estimate=stored,
            status="ok",
            confidence=_num(item.get("confidence")) or 0.6,
            drivers=[{"label": "Impacto persistido", "value": stored, "currency": item.get("impact_currency") or "USD"}],
            formula="Impacto persistido en control_room_items.",
            explanation="Estimacion recuperada del estado operativo persistido.",
            currency=str(item.get("impact_currency") or "USD"),
        )

    anomaly_type = str(item.get("anomaly_type") or "")
    cartridge = str(item.get("cartridge") or "")

    if cartridge == "replicon" and anomaly_type in {"low_margin", "wip_variance"}:
        revenue = _num(details.get("revenue_usd"))
        margin_usd = _num(details.get("margen_bruto_usd"))
        wip = _num(details.get("wip_usd")) or 0
        margin_gap = 0.0
        if revenue is not None and margin_usd is not None:
            margin_gap = max(0.0, revenue * 0.20 - margin_usd)
        exposure = margin_gap + (abs(wip) if abs(wip) >= 5000 else 0)
        if exposure > 0:
            return _impact_payload(
                item=item,
                estimate=exposure,
                status="ok",
                confidence=0.78,
                drivers=[
                    {"label": "Brecha margen objetivo 20%", "value": round(margin_gap, 2), "currency": "USD"},
                    {"label": "WIP bajo revision", "value": round(abs(wip), 2), "currency": "USD"},
                ],
                formula="max(0, revenue_usd * 20% - margen_bruto_usd) + abs(wip_usd si >= 5000)",
                explanation="Usa P&L Replicon materializado; no escribe en Replicon.",
            )

    if cartridge == "replicon" and anomaly_type == "non_billable_ratio":
        hours = _num(details.get("horas_no_facturables"))
        rate = (
            _num(details.get("billing_rate_usd"))
            or _num(details.get("billing_rate"))
            or _num(details.get("rate_usd"))
            or _num(details.get("currenthourlybillingamount"))
        )
        if hours is not None and rate is not None:
            return _impact_payload(
                item=item,
                estimate=hours * rate,
                status="ok",
                confidence=0.7,
                drivers=[
                    {"label": "Horas no facturables", "value": round(hours, 2)},
                    {"label": "Tarifa Replicon", "value": round(rate, 2), "currency": "USD"},
                ],
                formula="horas_no_facturables * tarifa_replicon",
                explanation="Calcula exposicion de horas no facturables con tarifa real disponible.",
            )

    if cartridge == "sap_s4hana" and anomaly_type == "negative_revenue":
        revenue = _num(details.get("revenue"))
        if revenue is not None:
            return _impact_payload(
                item=item,
                estimate=abs(revenue),
                status="ok",
                confidence=0.74,
                drivers=[{"label": "Revenue negativo", "value": revenue, "currency": "USD"}],
                formula="abs(revenue)",
                explanation="Usa revenue materializado por cliente/periodo.",
            )

    if cartridge == "sap_s4hana" and anomaly_type == "aged_sales_backlog":
        open_value = _num(details.get("open_value"))
        if open_value is not None:
            return _impact_payload(
                item=item,
                estimate=open_value,
                status="ok",
                confidence=0.62,
                drivers=[
                    {"label": "Backlog abierto", "value": round(open_value, 2), "currency": "USD"},
                    {"label": "Antiguedad maxima", "value": _num(details.get("oldest_age_days")) or 0, "unit": "dias"},
                ],
                formula="open_value",
                explanation="Exposicion comercial, no perdida confirmada.",
            )

    if cartridge == "sap_s4hana" and anomaly_type == "supplier_spend_concentration":
        spend = _num(details.get("total_spend"))
        if spend is not None:
            return _impact_payload(
                item=item,
                estimate=spend,
                status="ok",
                confidence=0.45,
                drivers=[{"label": "Gasto concentrado", "value": round(spend, 2), "currency": "USD"}],
                formula="total_spend",
                explanation="Exposicion de compras bajo revision, no ahorro garantizado.",
            )

    monthly_cost = (
        _num(details.get("monthly_cost_usd"))
        or _num(details.get("salary_monthly_usd"))
        or _num(details.get("costo_mensual_usd"))
    )
    if monthly_cost is not None and monthly_cost > 0:
        return _impact_payload(
            item=item,
            estimate=monthly_cost,
            status="ok",
            confidence=0.55,
            drivers=[{"label": "Costo mensual", "value": round(monthly_cost, 2), "currency": "USD"}],
            formula="monthly_cost_usd",
            explanation="Usa costo directo disponible en la fuente.",
        )

    return _impact_payload(
        item=item,
        estimate=None,
        status="unavailable",
        confidence=0.25,
        drivers=[],
        formula="Sin base monetaria disponible en el dataset.",
        explanation="La senal es operativa; falta cost basis para convertirla a dinero sin inventar cifras.",
    )


def _template_ids_for_item(item: dict[str, Any]) -> list[str]:
    anomaly_type = str(item.get("anomaly_type") or "")
    cartridge = str(item.get("cartridge") or "")
    if item.get("kind") == "source_state":
        return ["restore_data_source", "create_followup_task", "request_owner_review"]
    if cartridge == "replicon":
        if anomaly_type in {"low_margin", "wip_variance", "non_billable_ratio"}:
            return ["prepare_billing_review", "prepare_replicon_adjustment", "create_followup_task"]
        return ["prepare_replicon_adjustment", "request_owner_review", "create_followup_task"]
    if cartridge == "sap_s4hana":
        return ["prepare_sap_review", "request_owner_review", "create_followup_task"]
    return ["request_owner_review", "create_followup_task"]


def _action_templates_for_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(ACTION_TEMPLATES[template_id]) for template_id in _template_ids_for_item(item) if template_id in ACTION_TEMPLATES]


def _primary_template_for_item(item: dict[str, Any]) -> dict[str, Any]:
    templates = _action_templates_for_item(item)
    return templates[0] if templates else dict(ACTION_TEMPLATES["request_owner_review"])


def _execution_payload(item: dict[str, Any], mode: str, template: dict[str, Any]) -> dict[str, Any]:
    impact = _impact_for_item(item)
    return {
        "mode": mode,
        "external_writeback_enabled": _external_writeback_enabled(),
        "dry_run": mode != "execute_live",
        "template": template,
        "target_system": template.get("cartridge_id") if template.get("cartridge_id") != "platform" else item.get("cartridge"),
        "item": {
            "id": item.get("id"),
            "title": item.get("title"),
            "kind": item.get("kind"),
            "cartridge": item.get("cartridge"),
            "domain": item.get("domain"),
            "source_dataset": item.get("source_dataset"),
            "entity_kind": item.get("entity_kind"),
            "entity_id": item.get("entity_id"),
            "entity_label": item.get("entity_label"),
            "anomaly_type": item.get("anomaly_type"),
            "severity": item.get("severity"),
            "selected_option_id": item.get("selected_option_id"),
        },
        "impact": impact,
        "operations": [
            {
                "operation": template.get("action_kind"),
                "status": "preview" if mode == "preview" else "validated",
                "requires_approval": True,
                "external_write": False,
                "evidence": {
                    "sql": item.get("sql"),
                    "recommendation": item.get("recommendation"),
                    "root_cause": item.get("root_cause"),
                },
            }
        ],
        "guardrails": {
            "human_approval_required": True,
            "writeback_blocked_by_default": True,
            "feature_flag": "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK",
        },
    }


def _source_state_item(source: ControlRoomSource, status: str, error: str | None = None) -> dict[str, Any] | None:
    if status == "ok":
        return None
    severity = "high" if status in {"unavailable", "invalid_schema"} else "medium"
    title_by_status = {
        "empty": "Fuente sin datos materializados",
        "missing": "Dataset requerido no registrado",
        "unavailable": "Fuente operativa no disponible",
        "invalid_schema": "Dataset con contrato invalido",
    }
    description_by_status = {
        "empty": f"{source.dataset} existe pero no tiene filas para el workspace activo.",
        "missing": f"{source.dataset} no esta disponible en el catalogo del workspace activo.",
        "unavailable": f"{source.dataset} no pudo consultarse desde Refinement.",
        "invalid_schema": f"{source.dataset} no cumple el contrato esperado por la Sala de Control.",
    }
    item = _base_item(
        source,
        {"severity": severity, "details": {"source_status": status, "error": error or ""}},
        f"source_{status}",
        source.dataset,
        source.dataset,
    )
    item.update({
        "kind": "source_state",
        "title": title_by_status.get(status, "Fuente requiere atencion"),
        "description": description_by_status.get(status, f"{source.dataset} requiere revision."),
        "recommendation": "Validar instalacion, credenciales, materializacion y scope tenant/workspace antes de operar con el cliente.",
        "root_cause": "La cadena de datos no esta lista para entregar senales de negocio confiables.",
        "impact": "El cartucho puede aparecer activo pero sin datos accionables en la sala.",
    })
    return item


def _with_omega(item: dict[str, Any]) -> dict[str, Any]:
    decision_id = item.get("decision_id")
    status = item.get("status") or "open"
    approved = status == "approved"
    is_source_state = item.get("kind") == "source_state"
    primary_system = item.get("cartridge") or "platform"
    impact = _impact_for_item(item)
    action_templates = _action_templates_for_item(item)
    execution_status = str(item.get("execution_status") or "not_started")
    if execution_status not in EXECUTION_STATUSES:
        execution_status = "not_started"
    option_label = "Restaurar fuente de datos" if is_source_state else "Remediar dato/proceso"
    action_label = "Validar materializacion y permisos" if is_source_state else "Validar owner y remediacion"
    selected_option_id = str(item.get("selected_option_id") or "remediate")
    impact_money = (
        f"${impact['estimate']:,.0f} {impact['currency']} en revision"
        if impact.get("status") == "ok" and impact.get("estimate") is not None
        else "Impacto no calculable"
    )
    options = [
        {
            "id": "remediate",
            "label": option_label,
            "action": option_label,
            "money": impact_money,
            "time": "1-2 ciclos",
            "score": 92,
            "risk": "Bajo",
            "auto": True,
            "recommendation": item.get("recommendation"),
            "selected": False,
        },
        {
            "id": "exception",
            "label": "Aprobar excepcion temporal",
            "action": "Aprobar excepcion temporal",
            "money": "Costo medio",
            "time": "Mismo dia",
            "score": 68,
            "risk": "Medio",
            "auto": False,
            "recommendation": "Usar solo con responsable y fecha de control.",
            "selected": False,
        },
        {
            "id": "monitor",
            "label": "Monitorear sin cambio inmediato",
            "action": "Monitorear sin cambio inmediato",
            "money": "Sin gasto inmediato",
            "time": "Siguiente refresh",
            "score": 45,
            "risk": "Alto",
            "auto": False,
            "recommendation": "No recomendado para severidad alta o critica.",
            "selected": False,
        },
    ]
    if selected_option_id not in {option["id"] for option in options}:
        selected_option_id = "remediate"
    for option in options:
        option["selected"] = option["id"] == selected_option_id
    lessons = _lessons_for_item(item)
    return {
        **item,
        "impact_estimate": impact.get("estimate"),
        "impact_currency": impact.get("currency"),
        "impact_status": impact.get("status"),
        "confidence": impact.get("confidence"),
        "priority_score": impact.get("priority_score"),
        "impact_drivers": impact.get("drivers"),
        "impact_formula": impact.get("formula"),
        "impact_explanation": impact.get("explanation"),
        "selected_option_id": selected_option_id,
        "execution_status": execution_status,
        "action_templates": action_templates,
        "omega": {
            "signals": {
                "source": item.get("source_dataset"),
                "severity": item.get("severity"),
                "detected_at": item.get("detected_at"),
                "status": status,
                "priority_score": impact.get("priority_score"),
            },
            "investigation": {
                "root_cause": item.get("root_cause"),
                "impact": item.get("impact"),
                "evidence": item.get("details") or {},
                "money": impact,
            },
            "options": options,
            "decision": {
                "decision_id": decision_id,
                "status": status,
                "label": f"Decision #{decision_id}" if decision_id else "Pendiente",
            },
            "execution": {
                "status": execution_status,
                "external_writeback_enabled": _external_writeback_enabled(),
                "templates": action_templates,
                "actions": [
                    {
                        "id": "preview",
                        "sys": "omega",
                        "act": "Generar preview de accion",
                        "label": "Preview seguro",
                        "done": execution_status in {"preview_generated", "dry_run_validated", "executed"},
                        "approved": execution_status in {"preview_generated", "dry_run_validated", "executed"},
                        "auto": True,
                    },
                    {
                        "id": "dry_run",
                        "sys": primary_system,
                        "act": "Validar dry-run sin write-back",
                        "label": "Dry-run seguro",
                        "done": execution_status in {"dry_run_validated", "executed"},
                        "approved": execution_status in {"dry_run_validated", "executed"},
                        "auto": True,
                    },
                    {
                        "id": "owner_review",
                        "sys": primary_system,
                        "act": action_label,
                        "label": action_label,
                        "done": approved,
                        "approved": approved,
                        "auto": False,
                    },
                    {
                        "id": "audit_log",
                        "sys": "omega",
                        "act": "Registrar bitacora y evidencia",
                        "label": "Registrar bitacora y evidencia",
                        "done": bool(decision_id),
                        "approved": bool(decision_id),
                        "auto": True,
                    },
                ],
            },
            "control": {
                "owner": item.get("module") or item.get("cartridge"),
                "cadence": "Proximo refresh operativo",
                "status": "cerrado" if status in TERMINAL_ITEM_STATUSES else "abierto",
                "items": [
                    {
                        "id": "refresh",
                        "desc": "Confirmar que el siguiente refresh conserva o corrige la senal.",
                        "owner": item.get("module") or item.get("cartridge") or "operaciones",
                        "st": "cerrado" if status in TERMINAL_ITEM_STATUSES else "abierto",
                        "impact": item.get("impact") or "Riesgo operativo",
                        "days": 1 if approved else 3,
                    },
                    {
                        "id": "audit",
                        "desc": "Mantener evidencia ligada a decision_actions y audit_events.",
                        "owner": "omega",
                        "st": "listo" if decision_id else "pendiente",
                        "impact": "Trazabilidad",
                        "days": 0 if decision_id else 2,
                    },
                ],
            },
            "lessons": {
                "rules": lessons,
            },
        },
    }


def _status_sort_key(item: dict[str, Any]) -> tuple[int, int, str, str]:
    active_rank = 1 if item.get("status") in TERMINAL_ITEM_STATUSES else 0
    return (
        active_rank,
        -int(item.get("severity_weight") or 0),
        str(item.get("domain") or ""),
        str(item.get("title") or ""),
    )


async def _overlay_item_state(items: list[dict[str, Any]], user: dict | None, *, persist: bool = False) -> list[dict[str, Any]]:
    if not items:
        return []
    if not persist:
        baseline = [_with_omega(item) for item in items]
        baseline.sort(key=_status_sort_key)
        return baseline
    tenant_id, workspace_id = _workspace_scope(user)
    pool = await auth.pool()
    item_ids = [item["id"] for item in items]

    if persist:
        for item in items:
            impact = _impact_for_item(item)
            try:
                await pool.execute(
                    """
                    INSERT INTO control_room_items (
                        tenant_id, workspace_id, item_id, cartridge_id, domain,
                        source_dataset, item_kind, title, severity, status,
                        entity_kind, entity_id, entity_label, anomaly_type, metadata,
                        impact_estimate, impact_currency, confidence, priority_score,
                        selected_option_id, execution_status,
                        first_seen_at, last_seen_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8, $9, 'open',
                        $10, $11, $12, $13, $14::jsonb,
                        $15, $16, $17, $18, $19, $20,
                        NOW(), NOW()
                    )
                    ON CONFLICT (workspace_id, item_id) DO UPDATE
                    SET cartridge_id = EXCLUDED.cartridge_id,
                        domain = EXCLUDED.domain,
                        source_dataset = EXCLUDED.source_dataset,
                        item_kind = EXCLUDED.item_kind,
                        title = EXCLUDED.title,
                        severity = EXCLUDED.severity,
                        entity_kind = EXCLUDED.entity_kind,
                        entity_id = EXCLUDED.entity_id,
                        entity_label = EXCLUDED.entity_label,
                        anomaly_type = EXCLUDED.anomaly_type,
                        metadata = control_room_items.metadata || EXCLUDED.metadata,
                        impact_estimate = EXCLUDED.impact_estimate,
                        impact_currency = EXCLUDED.impact_currency,
                        confidence = EXCLUDED.confidence,
                        priority_score = EXCLUDED.priority_score,
                        last_seen_at = NOW(),
                        status = CASE
                            WHEN control_room_items.status = ANY($21::text[])
                            THEN control_room_items.status
                            ELSE control_room_items.status
                        END
                    """,
                    tenant_id,
                    workspace_id,
                    item["id"],
                    item["cartridge"],
                    item["domain"],
                    item["source_dataset"],
                    item["kind"],
                    item["title"],
                    item["severity"],
                    item.get("entity_kind"),
                    item.get("entity_id"),
                    item.get("entity_label"),
                    item.get("anomaly_type"),
                    json.dumps({
                        "module": item.get("module"),
                        "description": item.get("description"),
                        "recommendation": item.get("recommendation"),
                        "root_cause": item.get("root_cause"),
                        "impact": item.get("impact"),
                        "details": item.get("details") if isinstance(item.get("details"), dict) else {},
                        "sql": item.get("sql"),
                        "impact_payload": impact,
                    }),
                    impact.get("estimate"),
                    impact.get("currency") or "USD",
                    impact.get("confidence"),
                    impact.get("priority_score") or 0,
                    item.get("selected_option_id"),
                    item.get("execution_status") or "not_started",
                    sorted(TERMINAL_ITEM_STATUSES),
                )
            except Exception:
                break

    try:
        rows = await pool.fetch(
            """
            SELECT item_id, status, decision_id, metadata, first_seen_at, last_seen_at,
                   resolved_at, dismissed_at, impact_estimate, impact_currency,
                   confidence, priority_score, selected_option_id, execution_status
              FROM control_room_items
             WHERE workspace_id = $1
               AND item_id = ANY($2::text[])
            """,
            workspace_id,
            item_ids,
        )
        state_by_id = {row["item_id"]: _row_to_public(row) for row in rows}
    except Exception:
        state_by_id = {}

    merged = []
    for item in items:
        state = state_by_id.get(item["id"], {})
        metadata = _details(state.get("metadata"))
        status = str(state.get("status") or item.get("status") or "open")
        if status not in ITEM_STATUSES:
            status = "open"
        merged.append(_with_omega({
            **item,
            "status": status,
            "decision_id": state.get("decision_id") or item.get("decision_id"),
            "impact_estimate": state.get("impact_estimate"),
            "impact_currency": state.get("impact_currency"),
            "confidence": state.get("confidence"),
            "priority_score": state.get("priority_score"),
            "selected_option_id": state.get("selected_option_id") or metadata.get("selected_option_id"),
            "execution_status": state.get("execution_status") or metadata.get("execution_status"),
            "lessons": metadata.get("lessons"),
            "first_seen_at": state.get("first_seen_at"),
            "last_seen_at": state.get("last_seen_at"),
            "resolved_at": state.get("resolved_at"),
            "dismissed_at": state.get("dismissed_at"),
        }))
    merged.sort(key=_status_sort_key)
    return merged


async def _collect_items(
    user: dict | None,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
    limit_per_source: int = 1000,
    include_source_state_items: bool = False,
    persist: bool = False,
    use_catalog: bool = True,
) -> dict[str, Any]:
    if use_catalog:
        installations = await _installed_cartridges(user)
    else:
        installations = [
            {
                "cartridge_id": module.cartridge,
                "installation_status": "ready",
                "current_step": "test_registry",
                "label": module.label,
                "category": "platform" if module.operational else "cartridge",
            }
            for module in MODULES
            if module.sources
        ]
    active = {str(row.get("cartridge_id")) for row in installations}
    modules = [module for module in MODULES if module.cartridge in active]

    items: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    rows_by_dataset: dict[str, list[dict[str, Any]]] = {}
    for module in modules:
        for source in module.sources:
            rows, source_status = await _fetch_source(source, user, fetcher, limit_per_source)
            rows_by_dataset[source.dataset] = rows if source_status["status"] == "ok" else []
            sources.append(source_status)
            if include_source_state_items:
                source_item = _source_state_item(source, source_status["status"], source_status.get("error"))
                if source_item:
                    items.append(source_item)
            if source_status["status"] != "ok":
                continue
            for row in rows:
                item = _normalize_row(source, row)
                if item:
                    items.append(item)

    items = await _overlay_item_state(items, user, persist=persist)
    return {
        "items": items,
        "sources": sources,
        "installations": installations,
        "modules": modules,
        "financial": _financial_metrics(sources, rows_by_dataset),
    }


def _severity_counts(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {key: 0 for key in SEVERITY_WEIGHT}
    for item in items:
        counts[item["severity"]] = counts.get(item["severity"], 0) + 1
    return counts


def _domain_payload(
    domain: str,
    modules: list[ControlRoomModule],
    items: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    domain_modules = [module for module in modules if module.domain == domain or any(source.domain == domain for source in module.sources)]
    domain_items = [item for item in items if item["domain"] == domain]
    module_payload = []
    for module in domain_modules:
        module_sources = [source for source in sources if source["cartridge"] == module.cartridge and source.get("domain") == domain]
        module_items = [item for item in domain_items if item["cartridge"] == module.cartridge]
        source_count = sum(int(source.get("count") or 0) for source in module_sources)
        source_status = "inactive"
        if module_sources:
            if any(source["status"] in {"unavailable", "invalid_schema"} for source in module_sources):
                source_status = "attention"
            elif any(source["status"] in {"empty", "missing"} for source in module_sources):
                source_status = "empty"
            else:
                source_status = "ok"
        module_payload.append({
            "id": module.cartridge,
            "label": module.label,
            "domain": domain,
            "accent": module.accent,
            "item_count": len(module_items),
            "critical_count": sum(1 for item in module_items if item["severity"] == "critical"),
            "source_status": source_status,
            "kpis": [
                {
                    "label": "Registros fuente",
                    "value": source_count,
                    "tone": "neutral",
                    "bad": source_status in {"attention", "empty"},
                    "sql": " UNION ALL ".join(
                        f"SELECT COUNT(*) AS registros, '{source['dataset']}' AS dataset FROM {source['dataset']}"
                        for source in module_sources
                    ) or "-- sin fuente materializada",
                },
                {
                    "label": "Items abiertos",
                    "value": sum(1 for item in module_items if item["status"] not in TERMINAL_ITEM_STATUSES),
                    "tone": "attention",
                    "bad": any(item["status"] not in TERMINAL_ITEM_STATUSES for item in module_items),
                    "sql": f"SELECT * FROM control_room_items WHERE cartridge_id = '{module.cartridge}' AND domain = '{domain}' AND status NOT IN ('approved','dismissed','resolved')",
                },
            ],
        })
    return {
        "id": domain.lower().replace(" ", "_"),
        "label": domain,
        "accent": DOMAIN_ACCENTS.get(domain, "#64748b"),
        "item_count": len(domain_items),
        "critical_count": sum(1 for item in domain_items if item["severity"] == "critical"),
        "cartridge_count": len({module.cartridge for module in domain_modules}),
        "modules": module_payload,
    }


async def dashboard(
    user: dict | None,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
    limit_per_source: int = 1000,
    persist: bool = True,
) -> dict[str, Any]:
    payload = await _collect_items(
        user,
        fetcher=fetcher,
        limit_per_source=limit_per_source,
        include_source_state_items=True,
        persist=persist,
    )
    items = payload["items"]
    sources = payload["sources"]
    modules = payload["modules"]
    installations = payload["installations"]
    financial = payload["financial"]

    by_severity = _severity_counts(items)
    by_cartridge: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    for item in items:
        by_cartridge[item["cartridge"]] = by_cartridge.get(item["cartridge"], 0) + 1
        by_domain[item["domain"]] = by_domain.get(item["domain"], 0) + 1

    pool = await auth.pool()
    workspace_id = _workspace_id(user)
    open_decisions = 0
    try:
        open_decisions = int(await pool.fetchval(
            "SELECT COUNT(*) FROM decisions WHERE workspace_id = $1 AND status = 'open'",
            workspace_id,
        ) or 0)
    except Exception:
        open_decisions = 0

    module_by_id = _module_by_cartridge()
    cartridges = []
    for row in installations:
        cartridge_id = str(row.get("cartridge_id"))
        module = module_by_id.get(cartridge_id)
        module_items = [item for item in items if item["cartridge"] == cartridge_id]
        module_sources = [source for source in sources if source["cartridge"] == cartridge_id]
        source_status = "no_sources"
        if module_sources:
            if any(source["status"] in {"unavailable", "invalid_schema"} for source in module_sources):
                source_status = "attention"
            elif any(source["status"] in {"empty", "missing"} for source in module_sources):
                source_status = "empty"
            else:
                source_status = "ok"
        cartridges.append({
            "id": cartridge_id,
            "label": row.get("label") or (module.label if module else cartridge_id),
            "domain": module.domain if module else "Otros",
            "accent": module.accent if module else "#64748b",
            "status": row.get("installation_status") or "ready",
            "current_step": row.get("current_step"),
            "active": str(row.get("installation_status") or "ready") in ACTIVE_INSTALLATION_STATUSES,
            "operational": bool(module.operational) if module else False,
            "item_count": len(module_items),
            "critical_count": sum(1 for item in module_items if item["severity"] == "critical"),
            "source_status": source_status,
            "datasets": module_sources,
        })

    domain_labels = list(DOMAIN_ORDER)
    for module in modules:
        if module.domain not in domain_labels:
            domain_labels.append(module.domain)
        for source in module.sources:
            if source.domain not in domain_labels:
                domain_labels.append(source.domain)
    domains = [_domain_payload(domain, modules, items, sources) for domain in domain_labels]

    return {
        "workspace": {
            "tenant_id": (user or {}).get("active_tenant_id") or (user or {}).get("tenant_id"),
            "workspace_id": workspace_id,
        },
        "period": datetime.now(UTC).strftime("%B %Y"),
        "omega_steps": OMEGA_STEPS,
        "summary": {
            "total_items": len(items),
            "total_anomalies": sum(1 for item in items if item["kind"] == "anomaly"),
            "control_items": sum(1 for item in items if item["kind"] != "anomaly"),
            "by_severity": by_severity,
            "by_cartridge": by_cartridge,
            "by_domain": by_domain,
            "critical": by_severity.get("critical", 0),
            "attention": by_severity.get("high", 0) + by_severity.get("medium", 0),
            "open_decisions": open_decisions,
            "active_cartridges": len([row for row in cartridges if row["active"] and not row["operational"]]),
            "operational_cartridges": len([row for row in cartridges if row["active"] and row["operational"]]),
            "source_states": {
                status: sum(1 for source in sources if source["status"] == status)
                for status in ["ok", "empty", "missing", "unavailable", "invalid_schema"]
            },
            "cycle_counts": _cycle_counts(items),
            "financial": financial,
        },
        "domains": domains,
        "cartridges": cartridges,
        "sources": sources,
        "items": items,
    }


async def list_anomalies(
    user: dict | None,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
    limit_per_source: int = 1000,
) -> dict[str, Any]:
    payload = await _collect_items(
        user,
        fetcher=fetcher,
        limit_per_source=limit_per_source,
        include_source_state_items=False,
        persist=False,
        use_catalog=False,
    )
    anomalies = [item for item in payload["items"] if item["kind"] == "anomaly"]
    return {"anomalies": anomalies, "sources": payload["sources"]}


async def summary(user: dict | None, *, fetcher: DatasetFetcher = query_dataset_rows) -> dict[str, Any]:
    collected = await _collect_items(
        user,
        fetcher=fetcher,
        include_source_state_items=False,
        persist=False,
        use_catalog=False,
    )
    items = [item for item in collected["items"] if item["kind"] == "anomaly"]
    by_severity = _severity_counts(items)
    by_cartridge: dict[str, int] = {}
    by_domain: dict[str, int] = {}
    for item in items:
        by_cartridge[item["cartridge"]] = by_cartridge.get(item["cartridge"], 0) + 1
        by_domain[item["domain"]] = by_domain.get(item["domain"], 0) + 1
    pool = await auth.pool()
    workspace_id = _workspace_id(user)
    open_decisions = 0
    if workspace_id:
        open_decisions = int(await pool.fetchval(
            "SELECT COUNT(*) FROM decisions WHERE workspace_id = $1 AND status = 'open'",
            workspace_id,
        ) or 0)
    return {
        "total_anomalies": len(items),
        "by_severity": by_severity,
        "by_cartridge": by_cartridge,
        "by_domain": by_domain,
        "open_decisions": open_decisions,
        "sources": collected["sources"],
        "financial": collected["financial"],
    }


async def get_item(item_id: str, user: dict | None, *, fetcher: DatasetFetcher = query_dataset_rows) -> dict[str, Any]:
    payload = await dashboard(user, fetcher=fetcher, persist=True)
    for item in payload["items"]:
        if item["id"] == item_id:
            return item
    raise HTTPException(404, "control room item not found")


async def get_anomaly(anomaly_id: str, user: dict | None, *, fetcher: DatasetFetcher = query_dataset_rows) -> dict[str, Any]:
    item = await get_item(anomaly_id, user, fetcher=fetcher)
    if item["kind"] != "anomaly":
        raise HTTPException(404, "anomaly not found")
    return item


async def _persisted_item_for_mutation(item_id: str, user: dict) -> dict[str, Any] | None:
    workspace_id = _workspace_id(user)
    pool = await auth.pool()
    try:
        row = await pool.fetchrow(
            """
            SELECT item_id, cartridge_id, domain, source_dataset, item_kind, title,
                   severity, status, decision_id, entity_kind, entity_id,
                   entity_label, anomaly_type, metadata, first_seen_at, last_seen_at,
                   resolved_at, dismissed_at, impact_estimate, impact_currency,
                   confidence, priority_score, selected_option_id, execution_status
              FROM control_room_items
             WHERE workspace_id = $1
               AND item_id = $2
            """,
            workspace_id,
            item_id,
        )
    except Exception:
        return None
    if not row:
        return None
    try:
        row_item_id = row["item_id"]
    except Exception:
        return None
    if row_item_id != item_id:
        return None

    public_row = _row_to_public(row)
    metadata = _details(public_row.get("metadata"))
    severity = _severity(public_row["severity"])
    status = str(public_row["status"] or "open")
    if status not in ITEM_STATUSES:
        status = "open"
    escaped_item_id = item_id.replace("'", "''")
    item = {
        "id": public_row["item_id"],
        "kind": public_row["item_kind"],
        "domain": public_row["domain"],
        "module": metadata.get("module") or public_row["cartridge_id"],
        "cartridge": public_row["cartridge_id"],
        "source_dataset": public_row["source_dataset"],
        "entity_kind": public_row["entity_kind"] or "Entidad",
        "entity_id": public_row["entity_id"] or "",
        "entity_label": public_row["entity_label"] or public_row["entity_id"] or public_row["source_dataset"] or "Entidad",
        "anomaly_type": public_row["anomaly_type"] or "control_room_item",
        "severity": severity,
        "severity_weight": SEVERITY_WEIGHT[severity],
        "detected_at": "",
        "details": metadata.get("details") if isinstance(metadata.get("details"), dict) else {},
        "title": public_row["title"],
        "description": metadata.get("description") or public_row["title"],
        "recommendation": metadata.get("recommendation") or "Revisar, decidir y registrar evidencia.",
        "root_cause": metadata.get("root_cause") or "Senal persistida en Sala de Control.",
        "impact": metadata.get("impact") or "Riesgo operativo.",
        "sql": metadata.get("sql") or f"SELECT * FROM control_room_items WHERE item_id = '{escaped_item_id}'",
        "status": status,
        "decision_id": public_row["decision_id"],
        "impact_estimate": public_row.get("impact_estimate"),
        "impact_currency": public_row.get("impact_currency"),
        "confidence": public_row.get("confidence"),
        "priority_score": public_row.get("priority_score"),
        "selected_option_id": public_row.get("selected_option_id") or metadata.get("selected_option_id"),
        "execution_status": public_row.get("execution_status") or metadata.get("execution_status"),
        "lessons": metadata.get("lessons"),
        "first_seen_at": public_row.get("first_seen_at"),
        "last_seen_at": public_row.get("last_seen_at"),
        "resolved_at": public_row.get("resolved_at"),
        "dismissed_at": public_row.get("dismissed_at"),
    }
    return _with_omega(item)


async def _item_for_mutation(
    item_id: str,
    user: dict,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    persisted = await _persisted_item_for_mutation(item_id, user)
    if persisted:
        return persisted
    return await get_item(item_id, user, fetcher=fetcher)


async def _record_item_event(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    event_type: str,
    metadata: dict[str, Any],
) -> None:
    tenant_id, workspace_id = _workspace_scope(user)
    try:
        await pool.execute(
            """
            INSERT INTO control_room_item_events (
                tenant_id, workspace_id, item_id, event_type,
                actor_id, actor_email, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            """,
            tenant_id,
            workspace_id,
            item["id"],
            event_type,
            user.get("id"),
            user.get("email"),
            json.dumps(metadata),
        )
    except Exception:
        return


async def _persist_lessons(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    decision_id: int | None,
    lessons: list[str],
) -> None:
    tenant_id, workspace_id = _workspace_scope(user)
    for rule in lessons:
        try:
            await pool.execute(
                """
                INSERT INTO control_room_lessons (
                    tenant_id, workspace_id, item_id, cartridge_id, anomaly_type,
                    rule, source_decision_id, confidence, metadata
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                """,
                tenant_id,
                workspace_id,
                item["id"],
                item.get("cartridge") or "platform",
                item.get("anomaly_type") or "control_room_item",
                rule,
                decision_id,
                _impact_for_item(item).get("confidence") or 0.7,
                json.dumps({"source_dataset": item.get("source_dataset"), "status": item.get("status")}),
            )
        except Exception:
            return


async def _record_action_execution(
    pool: Any,
    *,
    user: dict,
    item: dict[str, Any],
    template: dict[str, Any],
    mode: str,
    status: str,
    payload: dict[str, Any],
    result: dict[str, Any],
    error: str | None = None,
) -> dict[str, Any]:
    tenant_id, workspace_id = _workspace_scope(user)
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO control_room_action_executions (
                tenant_id, workspace_id, item_id, template_id, mode, status,
                payload, result, error, actor_id, actor_email, completed_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10, $11, NOW())
            RETURNING *
            """,
            tenant_id,
            workspace_id,
            item["id"],
            template.get("template_id"),
            mode,
            status,
            json.dumps(payload),
            json.dumps(result),
            error,
            user.get("id"),
            user.get("email"),
        )
        return _row_to_public(row)
    except Exception:
        return {
            "id": None,
            "workspace_id": workspace_id,
            "item_id": item["id"],
            "template_id": template.get("template_id"),
            "mode": mode,
            "status": status,
            "payload": payload,
            "result": result,
            "error": error,
            "actor_email": user.get("email"),
            "created_at": datetime.now(UTC).isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
        }


async def _set_execution_status(pool: Any, *, user: dict, item: dict[str, Any], execution_status: str) -> None:
    workspace_id = _workspace_id(user)
    try:
        await pool.execute(
            """
            UPDATE control_room_items
               SET execution_status = $1,
                   metadata = COALESCE(metadata, '{}'::jsonb) || $4::jsonb,
                   last_seen_at = NOW()
             WHERE workspace_id = $2
               AND item_id = $3
            """,
            execution_status,
            workspace_id,
            item["id"],
            json.dumps({"execution_status": execution_status}),
        )
    except Exception:
        return


async def _ensure_item_row(pool: Any, *, user: dict, item: dict[str, Any], status: str = "open") -> None:
    tenant_id, workspace_id = _workspace_scope(user)
    impact = _impact_for_item(item)
    try:
        await pool.execute(
            """
            INSERT INTO control_room_items (
                tenant_id, workspace_id, item_id, cartridge_id, domain,
                source_dataset, item_kind, title, severity, status,
                entity_kind, entity_id, entity_label, anomaly_type, metadata,
                impact_estimate, impact_currency, confidence, priority_score,
                selected_option_id, execution_status
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15::jsonb,
                $16, $17, $18, $19, $20, $21
            )
            ON CONFLICT (workspace_id, item_id) DO UPDATE
            SET last_seen_at = NOW(),
                impact_estimate = EXCLUDED.impact_estimate,
                impact_currency = EXCLUDED.impact_currency,
                confidence = EXCLUDED.confidence,
                priority_score = EXCLUDED.priority_score,
                status = CASE
                    WHEN control_room_items.status = ANY($22::text[])
                    THEN control_room_items.status
                    ELSE EXCLUDED.status
                END
            """,
            tenant_id,
            workspace_id,
            item["id"],
            item["cartridge"],
            item["domain"],
            item["source_dataset"],
            item["kind"],
            item["title"],
            item["severity"],
            status,
            item.get("entity_kind"),
            item.get("entity_id"),
            item.get("entity_label"),
            item.get("anomaly_type"),
            json.dumps({
                "module": item.get("module"),
                "description": item.get("description"),
                "recommendation": item.get("recommendation"),
                "root_cause": item.get("root_cause"),
                "impact": item.get("impact"),
                "details": item.get("details") if isinstance(item.get("details"), dict) else {},
                "sql": item.get("sql"),
                "impact_payload": impact,
            }),
            impact.get("estimate"),
            impact.get("currency") or "USD",
            impact.get("confidence"),
            impact.get("priority_score") or 0,
            item.get("selected_option_id"),
            item.get("execution_status") or "not_started",
            sorted(TERMINAL_ITEM_STATUSES),
        )
    except Exception:
        return


async def create_decision_for_item(
    item_id: str,
    user: dict,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    workspace_id = _workspace_id(user)
    title = f"{item['title']} - {item['entity_label']}"
    description = (
        f"{item['description']}\n\n"
        f"Recomendacion OMEGA: {item['recommendation']}\n\n"
        f"Fuente: {item['source_dataset']} ({item['cartridge']})."
    )
    kpis = [{
        "label": "Severidad",
        "value": item["severity"],
        "source": item["source_dataset"],
    }, {
        "label": "Entidad",
        "value": item["entity_label"],
        "source": item["cartridge"],
    }, {
        "label": "Estado OMEGA",
        "value": item.get("status") or "open",
        "source": "control_room",
    }]
    pool = await auth.pool()
    row = await pool.fetchrow(
        """INSERT INTO decisions
              (title, description, commitment_date, kpis, created_by_id, assignee_id, visibility, workspace_id)
           VALUES ($1, $2, CURRENT_DATE + 7, $3::jsonb, $4, NULL, 'shared', $5)
           RETURNING *""",
        title,
        description,
        json.dumps(kpis),
        user["id"],
        workspace_id,
    )
    await pool.fetchrow(
        """INSERT INTO decision_actions (decision_id, action_text, note, actor)
           VALUES ($1, $2, $3, $4)
           RETURNING *""",
        row["id"],
        "Decision creada desde Sala de Control",
        item["recommendation"],
        user.get("email") or "user",
    )
    await _ensure_item_row(pool, user=user, item=item, status="decision_created")
    try:
        await pool.execute(
            """
            UPDATE control_room_items
               SET status = 'decision_created',
                   decision_id = $1,
                   last_seen_at = NOW()
             WHERE workspace_id = $2
               AND item_id = $3
            """,
            row["id"],
            workspace_id,
            item["id"],
        )
    except Exception:
        pass
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="decision_created",
        metadata={"decision_id": row["id"]},
    )
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.decision.create",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"decision_id": row["id"], "item": item},
        critical=True,
    )
    item = {**item, "decision_id": row["id"], "status": "decision_created"}
    return {"decision": dict(row), "item": _with_omega(item), "anomaly": _with_omega(item)}


async def select_item_option(
    item_id: str,
    option_id: str,
    user: dict,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    option_id = str(option_id or "").strip()
    valid_option_ids = {str(option["id"]) for option in item["omega"]["options"]}
    if option_id not in valid_option_ids:
        raise HTTPException(400, "invalid control room option")
    if item.get("status") in TERMINAL_ITEM_STATUSES:
        raise HTTPException(409, "terminal control room item cannot change option")

    pool = await auth.pool()
    workspace_id = _workspace_id(user)
    await _ensure_item_row(pool, user=user, item=item, status="in_review")
    try:
        await pool.execute(
            """
            UPDATE control_room_items
               SET status = CASE
                       WHEN status = ANY($4::text[]) THEN status
                       ELSE 'in_review'
                   END,
                   metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb,
                   selected_option_id = $5,
                   last_seen_at = NOW()
             WHERE workspace_id = $2
               AND item_id = $3
            """,
            json.dumps({"selected_option_id": option_id}),
            workspace_id,
            item["id"],
            sorted(TERMINAL_ITEM_STATUSES),
            option_id,
        )
    except Exception:
        pass
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="option_selected",
        metadata={"option_id": option_id},
    )
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.option.select",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"option_id": option_id, "item": item},
        critical=False,
    )
    item = _with_omega({**item, "selected_option_id": option_id, "status": "in_review"})
    return {"selected": True, "option_id": option_id, "item": item, "anomaly": item}


async def get_item_impact(
    item_id: str,
    user: dict,
    *,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    return _impact_for_item(item)


def _resolve_template(item: dict[str, Any], template_id: str | None) -> dict[str, Any]:
    templates = _action_templates_for_item(item)
    if not template_id:
        return templates[0] if templates else _primary_template_for_item(item)
    for template in templates:
        if template["template_id"] == template_id:
            return template
    raise HTTPException(400, "action template is not valid for this item")


async def action_preview(
    item_id: str,
    user: dict,
    *,
    template_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    template = _resolve_template(item, template_id)
    payload = _execution_payload(item, "preview", template)
    result = {
        "ok": True,
        "mode": "preview",
        "message": "Preview generado; no se ejecuto ningun cambio externo.",
        "external_write": False,
    }
    pool = await auth.pool()
    await _ensure_item_row(pool, user=user, item=item, status=item.get("status") or "in_review")
    execution = await _record_action_execution(
        pool,
        user=user,
        item=item,
        template=template,
        mode="preview",
        status="generated",
        payload=payload,
        result=result,
    )
    await _set_execution_status(pool, user=user, item=item, execution_status="preview_generated")
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="action_preview",
        metadata={"template_id": template["template_id"], "execution_id": execution.get("id")},
    )
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.action.preview",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"template_id": template["template_id"], "payload": payload},
        critical=True,
    )
    public_item = _with_omega({**item, "execution_status": "preview_generated"})
    return {"execution": execution, "payload": payload, "result": result, "item": public_item}


async def action_dry_run(
    item_id: str,
    user: dict,
    *,
    template_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    template = _resolve_template(item, template_id)
    payload = _execution_payload(item, "dry_run", template)
    warnings = []
    if payload["impact"]["status"] != "ok":
        warnings.append("impact_unavailable")
    if not item.get("decision_id"):
        warnings.append("decision_not_created_yet")
    result = {
        "ok": True,
        "mode": "dry_run",
        "validated": True,
        "external_write": False,
        "warnings": warnings,
        "message": "Dry-run validado. V1 no escribe en sistemas externos.",
    }
    pool = await auth.pool()
    await _ensure_item_row(pool, user=user, item=item, status=item.get("status") or "in_review")
    execution = await _record_action_execution(
        pool,
        user=user,
        item=item,
        template=template,
        mode="dry_run",
        status="validated",
        payload=payload,
        result=result,
    )
    await _set_execution_status(pool, user=user, item=item, execution_status="dry_run_validated")
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="action_dry_run",
        metadata={"template_id": template["template_id"], "execution_id": execution.get("id"), "warnings": warnings},
    )
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.action.dry_run",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"template_id": template["template_id"], "result": result},
        critical=True,
    )
    public_item = _with_omega({**item, "execution_status": "dry_run_validated"})
    return {"execution": execution, "payload": payload, "result": result, "item": public_item}


async def execute_item(
    item_id: str,
    user: dict,
    *,
    template_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    template = _resolve_template(item, template_id)
    payload = _execution_payload(item, "execute_live", template)
    pool = await auth.pool()
    await _ensure_item_row(pool, user=user, item=item, status=item.get("status") or "in_review")
    if not _external_writeback_enabled():
        result = {
            "ok": False,
            "mode": "execute_live",
            "external_write": False,
            "blocked": True,
            "message": "Write-back externo deshabilitado para V1.",
        }
        execution = await _record_action_execution(
            pool,
            user=user,
            item=item,
            template=template,
            mode="execute_live",
            status="blocked",
            payload=payload,
            result=result,
            error="CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK=false",
        )
        await _set_execution_status(pool, user=user, item=item, execution_status="blocked")
        await _record_item_event(
            pool,
            user=user,
            item=item,
            event_type="action_blocked",
            metadata={"template_id": template["template_id"], "execution_id": execution.get("id")},
        )
        await audit_service.record_event(
            user_id=user.get("id"),
            email=user.get("email"),
            action="control_room.action.execute.blocked",
            resource_type="control_room_item",
            resource_id=item_id,
            ip=ip,
            user_agent=user_agent,
            status="blocked",
            metadata={"template_id": template["template_id"], "result": result},
            critical=True,
        )
        raise HTTPException(409, "external write-back is disabled for Control Room V1")
    raise HTTPException(501, "live write-back connector is not implemented in V1")


async def create_decision_for_anomaly(
    anomaly_id: str,
    user: dict,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    return await create_decision_for_item(
        anomaly_id,
        user,
        ip=ip,
        user_agent=user_agent,
        fetcher=fetcher,
    )


async def approve_item(
    item_id: str,
    user: dict,
    *,
    decision_id: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    if decision_id is None:
        created = await create_decision_for_item(
            item_id,
            user,
            ip=ip,
            user_agent=user_agent,
            fetcher=fetcher,
        )
        decision_id = int(created["decision"]["id"])

    pool = await auth.pool()
    workspace_id = _workspace_id(user)
    visible = await pool.fetchrow(
        "SELECT id FROM decisions WHERE id = $1 AND workspace_id = $2",
        decision_id,
        workspace_id,
    )
    if not visible:
        raise HTTPException(404, "decision not found")
    lessons = _lessons_for_item(item)
    action = await pool.fetchrow(
        """INSERT INTO decision_actions (decision_id, action_text, note, actor)
           VALUES ($1, $2, $3, $4)
           RETURNING *""",
        decision_id,
        f"Aprobacion de recomendacion OMEGA: {item['title']}",
        item["recommendation"],
        user.get("email") or "user",
    )
    await _ensure_item_row(pool, user=user, item=item, status="approved")
    try:
        await pool.execute(
            """
            UPDATE control_room_items
               SET status = 'approved',
                   decision_id = $1,
                   metadata = COALESCE(metadata, '{}'::jsonb) || $4::jsonb,
                   resolved_at = COALESCE(resolved_at, NOW()),
                   last_seen_at = NOW()
             WHERE workspace_id = $2
               AND item_id = $3
            """,
            decision_id,
            workspace_id,
            item["id"],
            json.dumps({"lessons": lessons}),
        )
    except Exception:
        pass
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="approved",
        metadata={"decision_id": decision_id, "action_id": dict(action).get("id")},
    )
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="lesson_recorded",
        metadata={"decision_id": decision_id, "lessons": lessons},
    )
    await _persist_lessons(pool, user=user, item=item, decision_id=decision_id, lessons=lessons)
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.approve",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"decision_id": decision_id, "item": item},
        critical=True,
    )
    public_action = dict(action)
    if hasattr(public_action.get("ts"), "isoformat"):
        public_action["ts"] = public_action["ts"].isoformat()
    item = _with_omega({**item, "decision_id": decision_id, "status": "approved", "lessons": lessons})
    return {
        "approved": True,
        "decision_id": decision_id,
        "action": public_action,
        "item": item,
        "anomaly": item,
    }


async def approve_anomaly(
    anomaly_id: str,
    user: dict,
    *,
    decision_id: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    return await approve_item(
        anomaly_id,
        user,
        decision_id=decision_id,
        ip=ip,
        user_agent=user_agent,
        fetcher=fetcher,
    )


async def dismiss_item(
    item_id: str,
    user: dict,
    *,
    reason: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    pool = await auth.pool()
    workspace_id = _workspace_id(user)
    await _ensure_item_row(pool, user=user, item=item, status="dismissed")
    try:
        await pool.execute(
            """
            UPDATE control_room_items
               SET status = 'dismissed',
                   dismissed_at = COALESCE(dismissed_at, NOW()),
                   last_seen_at = NOW()
             WHERE workspace_id = $1
               AND item_id = $2
            """,
            workspace_id,
            item["id"],
        )
    except Exception:
        pass
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="dismissed",
        metadata={"reason": reason or ""},
    )
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.dismiss",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"reason": reason or "", "item": item},
        critical=True,
    )
    return {"dismissed": True, "item": _with_omega({**item, "status": "dismissed"})}


async def reopen_item(
    item_id: str,
    user: dict,
    *,
    reason: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    fetcher: DatasetFetcher = query_dataset_rows,
) -> dict[str, Any]:
    item = await _item_for_mutation(item_id, user, fetcher=fetcher)
    pool = await auth.pool()
    workspace_id = _workspace_id(user)
    await _ensure_item_row(pool, user=user, item=item, status="open")
    try:
        await pool.execute(
            """
            UPDATE control_room_items
               SET status = 'open',
                   decision_id = NULL,
                   resolved_at = NULL,
                   dismissed_at = NULL,
                   last_seen_at = NOW()
             WHERE workspace_id = $1
               AND item_id = $2
            """,
            workspace_id,
            item["id"],
        )
    except Exception:
        pass
    await _record_item_event(
        pool,
        user=user,
        item=item,
        event_type="reopened",
        metadata={"reason": reason or ""},
    )
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.reopen",
        resource_type="control_room_item",
        resource_id=item_id,
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata={"reason": reason or "", "item": item},
        critical=True,
    )
    return {"reopened": True, "item": _with_omega({**item, "status": "open", "decision_id": None})}


async def list_thresholds(user: dict) -> dict[str, Any]:
    workspace_id = _workspace_id(user)
    pool = await auth.pool()
    try:
        rows = await pool.fetch(
            """
            SELECT id, cartridge_id, anomaly_type, metric, warning_value,
                   critical_value, currency, enabled, metadata, created_at, updated_at
              FROM control_room_thresholds
             WHERE workspace_id = $1
             ORDER BY cartridge_id, anomaly_type, metric
            """,
            workspace_id,
        )
        return {"thresholds": [_row_to_public(row) for row in rows]}
    except Exception:
        return {"thresholds": [], "status": "unavailable"}


async def upsert_threshold(
    body: dict[str, Any],
    user: dict,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    tenant_id, workspace_id = _workspace_scope(user)
    cartridge_id = str(body.get("cartridge_id") or "").strip()
    anomaly_type = str(body.get("anomaly_type") or "").strip()
    metric = str(body.get("metric") or "").strip()
    if not cartridge_id or not anomaly_type or not metric:
        raise HTTPException(400, "cartridge_id, anomaly_type and metric are required")
    warning_value = _num(body.get("warning_value"))
    critical_value = _num(body.get("critical_value"))
    currency = str(body.get("currency") or "USD").strip().upper()[:8] or "USD"
    enabled = bool(body.get("enabled", True))
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    pool = await auth.pool()
    row = await pool.fetchrow(
        """
        INSERT INTO control_room_thresholds (
            tenant_id, workspace_id, cartridge_id, anomaly_type, metric,
            warning_value, critical_value, currency, enabled, metadata, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, NOW())
        ON CONFLICT (workspace_id, cartridge_id, anomaly_type, metric) DO UPDATE
        SET warning_value = EXCLUDED.warning_value,
            critical_value = EXCLUDED.critical_value,
            currency = EXCLUDED.currency,
            enabled = EXCLUDED.enabled,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
        RETURNING id, cartridge_id, anomaly_type, metric, warning_value,
                  critical_value, currency, enabled, metadata, created_at, updated_at
        """,
        tenant_id,
        workspace_id,
        cartridge_id,
        anomaly_type,
        metric,
        warning_value,
        critical_value,
        currency,
        enabled,
        json.dumps(metadata),
    )
    public = _row_to_public(row)
    await audit_service.record_event(
        user_id=user.get("id"),
        email=user.get("email"),
        action="control_room.threshold.upsert",
        resource_type="control_room_threshold",
        resource_id=str(public.get("id")),
        ip=ip,
        user_agent=user_agent,
        status="success",
        metadata=public,
        critical=True,
    )
    return {"threshold": public}


async def list_lessons(user: dict, *, cartridge_id: str | None = None) -> dict[str, Any]:
    workspace_id = _workspace_id(user)
    pool = await auth.pool()
    params: list[Any] = [workspace_id]
    where = ["workspace_id = $1"]
    if cartridge_id:
        params.append(cartridge_id)
        where.append(f"cartridge_id = ${len(params)}")
    try:
        rows = await pool.fetch(
            f"""
            SELECT id, item_id, cartridge_id, anomaly_type, rule,
                   source_decision_id, confidence, metadata, created_at
              FROM control_room_lessons
             WHERE {' AND '.join(where)}
             ORDER BY created_at DESC
             LIMIT 100
            """,
            *params,
        )
        return {"lessons": [_row_to_public(row) for row in rows]}
    except Exception:
        return {"lessons": [], "status": "unavailable"}
