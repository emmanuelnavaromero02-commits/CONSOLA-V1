from __future__ import annotations

from typing import Any

from app.domains.agentops.domain_monitor_support import DomainMonitorSpec

METRIC_LABELS: dict[str, str] = {
    "billable_hours_logged": "horas facturables registradas",
    "labor_cost_by_department": "costo laboral estimado de consultores",
    "project_margin": "margen por proyecto",
    "pipeline_health": "salud de las corridas de datos",
    "data_freshness_by_cartridge": "frescura de datos por cartucho",
    "absence_rate_company_by_type": "tasa de ausentismo por tipo",
    "attrition_risk_population": "poblacion en riesgo de rotacion",
    "employment_end_expiry": "fin de registro de empleo proximo",
    "deal_slippage": "deals con cierre vencido",
    "margen_bruto": "margen bruto",
    "margen_contribucion": "margen de contribución",
    "destructores": "destructores de margen",
    "concentracion_top20": "concentración del margen en el 20 % de clientes",
    "margen_vendedor": "margen por vendedor",
    "reconciliacion_finanzas": "reconciliación con Finanzas",
    "calidad_datos": "calidad de datos",
    "modelo_entidades": "modelo de entidades",
    "ratio_sellout_sellin": "ratio sell-out / sell-in",
    "dias_inventario": "días de inventario en canal",
    "sellout_clinica": "sell-out por clínica",
    "semaforo_distribuidoras": "semáforo de distribuidoras",
    "caducidad_lotes": "caducidad de lotes",
    "dias_cobertura": "días de cobertura",
    "oc_vs_necesidad": "órdenes de compra contra necesidad",
    "costo_real_vs_estandar": "costo real contra estándar",
    "lead_time_proveedores": "entregas de proveedores",
    "aprendizaje": "aprendizaje de decisiones",
}

VIEW_BY_KEY: dict[str, str] = {
    "finance": "finance_kpis",
    "operations": "operations_kpis",
    "risk": "risk_kpis",
    "sap_b1_margin": "sap_b1_margin_kpis",
    "sap_b1_expiry": "sap_b1_expiry_kpis",
    "sap_b1_supply": "sap_b1_supply_kpis",
    "sap_b1_learning": "sap_b1_learning_kpis",
    "sap_b1_semaforo": "sap_b1_semaforo_kpis",
}
AREAS_BY_KEY: frozenset[str] = frozenset({"sap_b1_semaforo"})
MAX_AREA_FINDINGS = 5

STATUS_READY = "ready"
STATUS_DEGRADED = "degraded"
STATUS_UNAVAILABLE = "unavailable"

MAX_SIGNALS = 10


def metric_label(metric: str) -> str:
    return METRIC_LABELS.get(metric, metric)


def _metric_reason(item: dict[str, Any]) -> str | None:
    error = item.get("error")
    if isinstance(error, str) and error.strip():
        return error.strip()
    notes = item.get("notes")
    if isinstance(notes, list):
        for note in notes:
            if isinstance(note, str) and note.strip():
                return note.strip()
    return None


def build_signals(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for metric, item in metrics.items():
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower()
        breaches = [
            breach.strip()
            for breach in (item.get("breaches") or [])
            if isinstance(breach, str) and breach.strip()
        ]
        if status != STATUS_READY:
            signal: dict[str, Any] = {
                "metric": metric_label(metric),
                "status": status or "unknown",
            }
            reason = _metric_reason(item)
            if reason:
                signal["reason"] = reason
            signals.append(signal)
        for breach in breaches:
            if len(signals) >= MAX_SIGNALS:
                break
            signals.append({"metric": metric_label(metric), "status": "alerta", "reason": breach})
        if len(signals) >= MAX_SIGNALS:
            break
    return signals[:MAX_SIGNALS]


def build_coverage(metrics: dict[str, Any]) -> dict[str, list[str]]:
    coverage: dict[str, list[str]] = {"ready": [], "degraded": [], "unavailable": []}
    for metric, item in metrics.items():
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower()
        bucket = coverage.get(status)
        if bucket is not None:
            bucket.append(metric_label(metric))
    return coverage


def area_color(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "").strip().lower()
    if status == STATUS_UNAVAILABLE:
        return "sin_datos"
    if any(isinstance(b, str) and b.strip() for b in (item.get("breaches") or [])):
        return "rojo"
    return "amarillo" if status == STATUS_DEGRADED else "verde"


def build_areas(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    areas = []
    for metric, item in metrics.items():
        if not isinstance(item, dict):
            continue
        findings = [b.strip() for b in (item.get("breaches") or []) if isinstance(b, str) and b.strip()]
        areas.append({
            "metric": metric,
            "label": metric_label(metric),
            "color": area_color(item),
            "period": item.get("period") or item.get("as_of"),
            "findings": findings[:MAX_AREA_FINDINGS],
            "findings_total": len(findings),
            "reason": None if findings else _metric_reason(item),
        })
    return areas


def build_payload(spec: DomainMonitorSpec, view: dict[str, Any]) -> dict[str, Any]:
    metrics = view.get("metrics") if isinstance(view.get("metrics"), dict) else {}
    status = str(view.get("status") or "unknown").strip().lower()
    signals = build_signals(metrics)
    coverage = build_coverage(metrics)
    blockers = [
        f"{metric_label(metric)}: sin evidencia disponible"
        for metric in (view.get("unavailable_metrics") or [])
        if isinstance(metric, str)
    ]
    payload = {
        "ok": True,
        "wisdom_bit_id": spec.wisdom_bit_id,
        "cartridge_id": spec.cartridge_id,
        "domain": spec.domain,
        "decision_mode": "recommendation_only",
        "writeback_enabled": False,
        "compensation_enabled": False,
        "status": status,
        "data_sufficient": status != STATUS_UNAVAILABLE,
        "generated_at": view.get("generated_at"),
        "profile": {
            "domain": spec.domain,
            "metrics_total": len(metrics),
            "metrics_ready": len(coverage["ready"]),
            "metrics_degraded": len(coverage["degraded"]),
            "metrics_unavailable": len(coverage["unavailable"]),
        },
        "coverage": {"status": status, **coverage},
        "blockers": blockers,
        "signals": {"count": len(signals), "items": signals},
        "notes": [note for note in (view.get("notes") or []) if isinstance(note, str)],
        "evidence": {"recommendation_only": True},
    }
    if spec.key in AREAS_BY_KEY:
        payload["areas"] = build_areas(metrics)
    return payload


async def domain_wisdom_bit(
    user: dict | None,
    spec: DomainMonitorSpec,
    *,
    control_room_service: Any,
) -> dict[str, Any]:
    view_name = VIEW_BY_KEY.get(spec.key)
    if not view_name:
        raise KeyError(f"no domain KPI view for monitor {spec.key!r}")
    view = await getattr(control_room_service, view_name)(user)
    payload = build_payload(spec, view if isinstance(view, dict) else {})

    if spec.key == "finance":
        from app.services.intelligence import domain_memory_hooks

        await domain_memory_hooks.record_cost_center_budget_gap(user)
    return payload


__all__ = (
    "AREAS_BY_KEY",
    "MAX_SIGNALS",
    "METRIC_LABELS",
    "VIEW_BY_KEY",
    "area_color",
    "build_areas",
    "build_coverage",
    "build_payload",
    "build_signals",
    "domain_wisdom_bit",
    "metric_label",
)
