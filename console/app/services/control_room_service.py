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
    data = payload.get("data", payload.get("result", payload))
    if isinstance(data, dict):
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
    return None


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
    return {
        **item,
        "omega": {
            "signals": {
                "source": item.get("source_dataset"),
                "severity": item.get("severity"),
                "detected_at": item.get("detected_at"),
                "status": status,
            },
            "investigation": {
                "root_cause": item.get("root_cause"),
                "impact": item.get("impact"),
                "evidence": item.get("details") or {},
            },
            "options": [
                {
                    "id": "remediate",
                    "label": "Remediar dato/proceso",
                    "score": 92,
                    "risk": "Bajo",
                    "recommendation": item.get("recommendation"),
                    "selected": True,
                },
                {
                    "id": "exception",
                    "label": "Aprobar excepcion temporal",
                    "score": 68,
                    "risk": "Medio",
                    "recommendation": "Usar solo con responsable y fecha de control.",
                    "selected": False,
                },
                {
                    "id": "monitor",
                    "label": "Monitorear sin cambio inmediato",
                    "score": 45,
                    "risk": "Alto",
                    "recommendation": "No recomendado para severidad alta o critica.",
                    "selected": False,
                },
            ],
            "decision": {
                "decision_id": decision_id,
                "status": status,
                "label": f"Decision #{decision_id}" if decision_id else "Pendiente",
            },
            "execution": {
                "actions": [
                    {
                        "id": "owner_review",
                        "label": "Validar owner y remediacion",
                        "approved": approved,
                    },
                    {
                        "id": "audit_log",
                        "label": "Registrar bitacora y evidencia",
                        "approved": bool(decision_id),
                    },
                ],
            },
            "control": {
                "owner": item.get("module") or item.get("cartridge"),
                "cadence": "Proximo refresh operativo",
                "status": "cerrado" if status in TERMINAL_ITEM_STATUSES else "abierto",
            },
            "lessons": {
                "rules": [
                    f"Si {item.get('source_dataset')} genera {item.get('anomaly_type')}, abrir revision OMEGA.",
                    "Toda aprobacion debe quedar ligada a decision_actions y audit_events.",
                ],
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
            try:
                await pool.execute(
                    """
                    INSERT INTO control_room_items (
                        tenant_id, workspace_id, item_id, cartridge_id, domain,
                        source_dataset, item_kind, title, severity, status,
                        entity_kind, entity_id, entity_label, anomaly_type, metadata,
                        first_seen_at, last_seen_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8, $9, 'open',
                        $10, $11, $12, $13, $14::jsonb,
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
                        last_seen_at = NOW(),
                        status = CASE
                            WHEN control_room_items.status = ANY($15::text[])
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
                        "description": item.get("description"),
                        "recommendation": item.get("recommendation"),
                        "root_cause": item.get("root_cause"),
                        "impact": item.get("impact"),
                    }),
                    sorted(TERMINAL_ITEM_STATUSES),
                )
            except Exception:
                break

    try:
        rows = await pool.fetch(
            """
            SELECT item_id, status, decision_id, first_seen_at, last_seen_at, resolved_at, dismissed_at
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
        status = str(state.get("status") or item.get("status") or "open")
        if status not in ITEM_STATUSES:
            status = "open"
        merged.append(_with_omega({
            **item,
            "status": status,
            "decision_id": state.get("decision_id") or item.get("decision_id"),
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
    for module in modules:
        for source in module.sources:
            rows, source_status = await _fetch_source(source, user, fetcher, limit_per_source)
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
                {"label": "Registros fuente", "value": source_count, "tone": "neutral"},
                {"label": "Items abiertos", "value": sum(1 for item in module_items if item["status"] not in TERMINAL_ITEM_STATUSES), "tone": "attention"},
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


async def _ensure_item_row(pool: Any, *, user: dict, item: dict[str, Any], status: str = "open") -> None:
    tenant_id, workspace_id = _workspace_scope(user)
    try:
        await pool.execute(
            """
            INSERT INTO control_room_items (
                tenant_id, workspace_id, item_id, cartridge_id, domain,
                source_dataset, item_kind, title, severity, status,
                entity_kind, entity_id, entity_label, anomaly_type, metadata
            )
            VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10,
                $11, $12, $13, $14, $15::jsonb
            )
            ON CONFLICT (workspace_id, item_id) DO UPDATE
            SET last_seen_at = NOW(),
                status = CASE
                    WHEN control_room_items.status = ANY($16::text[])
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
            json.dumps({"description": item.get("description"), "recommendation": item.get("recommendation")}),
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
    item = await get_item(item_id, user, fetcher=fetcher)
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
    item = await get_item(item_id, user, fetcher=fetcher)
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
                   resolved_at = COALESCE(resolved_at, NOW()),
                   last_seen_at = NOW()
             WHERE workspace_id = $2
               AND item_id = $3
            """,
            decision_id,
            workspace_id,
            item["id"],
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
    item = _with_omega({**item, "decision_id": decision_id, "status": "approved"})
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
    item = await get_item(item_id, user, fetcher=fetcher)
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
        critical=False,
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
    item = await get_item(item_id, user, fetcher=fetcher)
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
        critical=False,
    )
    return {"reopened": True, "item": _with_omega({**item, "status": "open", "decision_id": None})}
