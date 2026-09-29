from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from app.services.mcp_gateway import adapters, catalog, sources, sync_copy
from app.services.mcp_gateway.errors import GatewayError
from app.services.public_text_sensitivity import contains_public_technical_copy


MAX_LIST_ITEMS = 10
MAX_TABLES = 100
MAX_SNIPPET_CHARS = 1_200
MAX_TEXT_CHARS = 300
FAILED_RUN_STATUSES = frozenset({"failed", "error", "upstream_failed", "partial"})
PROBLEM_STATES = frozenset({"unavailable", "paused_by_operator"})
SIN_INFORMACION = "Sin información"
REUSE_CLOCK_SKEW = timedelta(seconds=2)
UNIONIZED_SOURCES = ["Escalafón", "Tabulador salarial", "Contrato colectivo"]
LAYER_LABELS = {"gold": "gold", "silver": "silver"}
VALUE_COPY: dict[str, str] = {
    "red": "rojo",
    "yellow": "amarillo",
    "green": "verde",
    "ok": "disponible",
    "ready": "disponible",
    "available": "disponible",
    "complete": "disponible",
    "unavailable": "sin_informacion",
    "missing": "sin_informacion",
    "empty": "sin_datos",
    "degraded": "degradado",
    "partial": "parcial",
    "pending": "pendiente",
    "stale": "desactualizado",
    "blocked": "bloqueado",
}
TRANSLATED_VALUE_KEYS = frozenset({"estado", "color", "color_general"})
KEY_COPY: dict[str, str] = {
    "generated_at": "generado_en",
    "status": "estado",
    "notes": "notas",
    "unavailable_metrics": "metricas_no_disponibles",
    "degraded_metrics": "metricas_degradadas",
    "metrics": "metricas",
    "supported": "soportado",
    "proxy_note": "nota_de_aproximacion",
    "period": "periodo",
    "breaches": "incumplimientos",
    "company": "empresa",
    "value": "valor",
    "pct": "porcentaje",
    "revenue": "ingreso",
    "commission": "comision",
    "currency": "moneda",
    "group": "grupo",
    "companies": "empresas",
    "trailing_group_pct": "porcentaje_grupo_movil",
    "customers": "clientes",
    "customer": "cliente",
    "margin_lost": "margen_perdido",
    "min_margin_pct": "margen_minimo_pct",
    "margin_pct": "margen_pct",
    "by_company": "por_empresa",
    "top": "principales",
    "share_pct": "participacion_pct",
    "top_margin": "margen_principales",
    "top_customers": "clientes_principales",
    "seller": "vendedor",
    "margin": "margen",
    "contribution": "contribucion",
    "sellers": "vendedores",
    "best_pct": "mejor_pct",
    "worst_pct": "peor_pct",
    "sellers_detail": "detalle_vendedores",
    "indicator": "indicador",
    "dimension": "dimension",
    "key": "clave",
    "label": "etiqueta",
    "unit": "unidad",
    "finance_value": "valor_finanzas",
    "platform_value": "valor_plataforma",
    "delta_pct": "diferencia_pct",
    "venta_bruta": "venta_bruta",
    "devoluciones_nc": "devoluciones_nc",
    "descuentos_pie_factura": "descuentos_pie_factura",
    "costo_aplicado": "costo_aplicado",
    "comision": "comision",
    "rows": "filas",
    "within": "dentro_de_tolerancia",
    "outside": "fuera_de_tolerancia",
    "without_platform": "sin_plataforma",
    "platform_only": "solo_plataforma",
    "within_pct": "dentro_de_tolerancia_pct",
    "tolerance_pct": "tolerancia_pct",
    "periods": "periodos",
    "outliers": "atipicos",
    "ledger_months": "meses_contables",
    "ledger_differences": "diferencias_contables",
    "check": "verificacion",
    "total": "total",
    "failing": "fallidas",
    "pct_ok": "porcentaje_correcto",
    "min_pct": "minimo_pct",
    "checks": "verificaciones",
    "checks_below_min": "verificaciones_bajo_minimo",
    "entity": "entidad",
    "records": "registros",
    "identities": "identidades",
    "shared_identities": "identidades_compartidas",
    "complete_records": "registros_completos",
    "completeness_pct": "completitud_pct",
    "orphans": "huerfanos",
    "relation_rule": "regla_de_relacion",
    "entities": "entidades",
    "margen_bruto": "margen_bruto",
    "margen_contribucion": "margen_contribucion",
    "destructores": "destructores",
    "concentracion_top20": "concentracion_top20",
    "margen_vendedor": "margen_vendedor",
    "reconciliacion_finanzas": "reconciliacion_finanzas",
    "calidad_datos": "calidad_datos",
    "modelo_entidades": "modelo_entidades",
    "growth_color": "color_crecimiento",
    "sellout_sellin_color": "color_sellout_sellin",
    "channel_days_color": "color_dias_canal",
    "margin_color": "color_margen",
    "expiry_color": "color_caducidad",
    "distributor": "distribuidora",
    "sell_out_revenue": "ingreso_sell_out",
    "sell_out_qty": "cantidad_sell_out",
    "sell_in_qty": "cantidad_sell_in",
    "growth_mom_pct": "crecimiento_mensual_pct",
    "growth_yoy_pct": "crecimiento_anual_pct",
    "sellout_sellin_3m_pct": "sellout_sellin_3m_pct",
    "channel_days": "dias_canal",
    "expiry_exposed_pct": "expuesto_a_caducidad_pct",
    "colors": "colores",
    "overall_color": "color_general",
    "distributors": "distribuidoras",
    "red": "rojo",
    "yellow": "amarillo",
    "green": "verde",
    "without_thresholds": "sin_umbrales",
    "color": "color",
    "clinic": "clinica",
    "units": "unidades",
    "clinics": "clinicas",
    "top_share_pct": "participacion_principal_pct",
    "by_distributor": "por_distribuidora",
    "ratio_sellout_sellin": "ratio_sellout_sellin",
    "dias_inventario": "dias_inventario",
    "sellout_clinica": "sellout_clinica",
    "semaforo_distribuidoras": "semaforo_distribuidoras",
    "batches": "lotes",
    "at_risk_value": "valor_en_riesgo",
    "vencido": "vencido",
    "rojo": "rojo",
    "amarillo": "amarillo",
    "verde": "verde",
    "sin_consumo": "sin_consumo",
    "traslado_filial": "traslado_filial",
    "traslado_empresa": "traslado_empresa",
    "promocion": "promocion",
    "item": "articulo",
    "batch": "lote",
    "branch": "sucursal",
    "level": "nivel",
    "days_to_expiry": "dias_para_caducar",
    "at_risk_qty": "cantidad_en_riesgo",
    "option": "opcion",
    "action": "accion",
    "as_of": "fecha_de_corte",
    "levels": "niveles",
    "options": "opciones",
    "priorities": "prioridades",
    "caducidad_lotes": "caducidad_lotes",
    "name": "nombre",
    "stockout_risk": "riesgo_de_desabasto",
    "coverage_days": "dias_de_cobertura",
    "lead_time_days": "dias_de_entrega",
    "stockout_date": "fecha_de_desabasto",
    "suggested_qty": "cantidad_sugerida",
    "supplier": "proveedor",
    "alternate_supplier": "proveedor_alterno",
    "order_by": "ordenar_antes_de",
    "basis": "base",
    "critical": "critico",
    "plan_changed": "plan_cambiado",
    "critical_at_risk": "criticos_en_riesgo",
    "plan_changes": "cambios_de_plan",
    "suggestions": "sugerencias",
    "suggested_value": "valor_sugerido",
    "risks": "riesgos",
    "net_need_qty": "necesidad_neta",
    "open_po_qty": "ordenes_abiertas",
    "coverage_pct": "cobertura_pct",
    "items_with_need": "articulos_con_necesidad",
    "items_short": "articulos_faltantes",
    "shortfalls": "faltantes",
    "variance_pct": "variacion_pct",
    "variance_value": "variacion_valor",
    "raw_material": "materia_prima",
    "items": "articulos",
    "above_threshold": "sobre_umbral",
    "worst": "peores",
    "intercompany": "intercompania",
    "receipts": "recepciones",
    "late_receipts": "recepciones_tardias",
    "on_time_pct": "a_tiempo_pct",
    "avg_lead_days": "dias_entrega_promedio",
    "avg_promised_days": "dias_prometidos_promedio",
    "max_delay_days": "retraso_maximo_dias",
    "suppliers": "proveedores",
    "late_suppliers": "proveedores_tardios",
    "dias_cobertura": "dias_cobertura",
    "oc_vs_necesidad": "oc_vs_necesidad",
    "costo_real_vs_estandar": "costo_real_vs_estandar",
    "lead_time_proveedores": "lead_time_proveedores",
    "source": "fuente",
    "alerts": "alertas",
    "decisions": "decisiones",
    "outcomes": "resultados",
    "false_positives": "falsos_positivos",
    "achieved": "logrados",
    "not_achieved": "no_logrados",
    "thresholds": "umbrales",
    "reason": "motivo",
    "window_days": "ventana_dias",
    "sources": "fuentes",
    "aprendizaje": "aprendizaje",
}
SAP_B1_VIEW_LABELS = {
    "sap_b1_margin_kpis": "finanzas",
    "sap_b1_sales_kpis": "ventas",
    "sap_b1_expiry_kpis": "caducidad",
    "sap_b1_supply_kpis": "compras",
    "sap_b1_learning_kpis": "aprendizaje",
    "sap_b1_semaforo_kpis": "semaforo",
}


@dataclass
class ActionResult:
    resumen: str
    datos: dict[str, Any]
    truncado: bool = False


class _Clip:
    def __init__(self) -> None:
        self.truncated = False

    def items(self, values: Any, limit: int = MAX_LIST_ITEMS) -> list[Any]:
        items = list(values) if isinstance(values, (list, tuple)) else []
        if len(items) > limit:
            self.truncated = True
        return items[:limit]

    def text(self, value: Any, limit: int = MAX_TEXT_CHARS) -> str | None:
        if not isinstance(value, str):
            return None
        text = " ".join(value.split())
        if len(text) > limit:
            self.truncated = True
            return text[: limit - 1].rstrip() + "…"
        return text


def _translate(value: Any, clip: _Clip, *, key: str | None = None, depth: int = 0) -> Any:
    if depth > 8:
        return None
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for raw_key, item in value.items():
            target = KEY_COPY.get(str(raw_key))
            if target is None:
                continue
            translated = _translate(item, clip, key=target, depth=depth + 1)
            if target == "notas" and isinstance(translated, list):
                translated = [
                    note for note in translated
                    if isinstance(note, str) and not contains_public_technical_copy(note)
                ]
            out[target] = translated
        return out
    if isinstance(value, (list, tuple)):
        return [_translate(item, clip, key=key, depth=depth + 1) for item in clip.items(value)]
    if isinstance(value, str):
        if key in TRANSLATED_VALUE_KEYS or (key or "").startswith("color_"):
            return VALUE_COPY.get(value, value)
        return clip.text(value)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return None


def _args(model: type[BaseModel], args: BaseModel) -> Any:
    if not isinstance(args, model):
        raise GatewayError(400, "argumentos_invalidos")
    return args


async def consultar_contexto(user: dict[str, Any], args: BaseModel) -> ActionResult:
    _args(catalog.ConsultarContextoArgs, args)
    checked = adapters.require_gateway_user(user)
    scopes = sorted(catalog.token_scopes(checked))
    labels = sources.enabled_source_labels(checked)
    visible = [action.name for action in catalog.visible_actions(checked)]
    workspace = adapters.workspace_summary(checked)
    alcance = (
        "lectura y acciones" if catalog.SCOPE_ACTIONS in scopes else "solo lectura"
    )
    return ActionResult(
        resumen=(
            f"Espacio de trabajo «{workspace['nombre']}» con {len(labels)} fuentes "
            f"habilitadas; alcance: {alcance}."
        ),
        datos={
            "espacio_de_trabajo": workspace,
            "usuario": checked.get("name") or SIN_INFORMACION,
            "alcances": scopes,
            "token": {
                "prefijo": checked.get("access_token_prefix"),
                "vence_en": checked.get("access_token_expires_at"),
            },
            "fuentes_habilitadas": labels,
            "acciones_disponibles": visible,
        },
    )


def _automation_problem(item: Mapping[str, Any]) -> bool:
    last = item.get("last_run") if isinstance(item.get("last_run"), Mapping) else {}
    return str(item.get("state") or "") in PROBLEM_STATES or str(
        (last or {}).get("status") or ""
    ) in FAILED_RUN_STATUSES


def _automation_row(item: Mapping[str, Any], clip: _Clip) -> dict[str, Any]:
    last = item.get("last_run") if isinstance(item.get("last_run"), Mapping) else None
    cartridge = str(item.get("cartridge_id") or "")
    return {
        "nombre": clip.text(item.get("label")) or SIN_INFORMACION,
        "fuente": sources.source_label(cartridge) if cartridge else "Plataforma",
        "tipo": sync_copy.AUTOMATION_KIND_COPY.get(str(item.get("kind") or ""), SIN_INFORMACION),
        "frecuencia": clip.text(item.get("schedule_description")) or SIN_INFORMACION,
        "estado": sync_copy.AUTOMATION_STATE_COPY.get(str(item.get("state") or ""), SIN_INFORMACION),
        "nota": clip.text(item.get("state_note_es")) or SIN_INFORMACION,
        "corridas_activas": item.get("active_runs"),
        "ultima_corrida": (
            {
                "estado": sync_copy.status_copy(last.get("status")),
                "inicio": last.get("started_at"),
                "fin": last.get("finished_at"),
            }
            if last
            else None
        ),
        "con_problemas": _automation_problem(item),
    }


async def consultar_salud_pipelines(user: dict[str, Any], args: BaseModel) -> ActionResult:
    parsed = _args(catalog.ConsultarSaludPipelinesArgs, args)
    source_id = sources.resolve_source(parsed.fuente, user) if parsed.fuente.strip() else None
    payload = await adapters.pipeline_automations(user)
    items = [item for item in payload.get("automations") or [] if isinstance(item, Mapping)]
    if source_id:
        items = [item for item in items if str(item.get("cartridge_id") or "") == source_id]
    problems = [item for item in items if _automation_problem(item)]
    selected = problems if parsed.solo_con_problemas else items
    clip = _Clip()
    rows = [_automation_row(item, clip) for item in clip.items(selected, 50)]
    available = bool(payload.get("airflow_available"))
    resumen = (
        f"{len(problems)} de {len(items)} automatizaciones con problemas."
        if available
        else "No se pudo consultar el orquestador; el estado de las automatizaciones es desconocido."
    )
    return ActionResult(
        resumen=resumen,
        datos={
            "orquestador_disponible": available,
            "consultado_en": payload.get("checked_at"),
            "total": len(items),
            "con_problemas": len(problems),
            "automatizaciones": rows,
        },
        truncado=clip.truncated,
    )


def _breakdown(values: Any, clip: _Clip) -> list[dict[str, Any]]:
    return [
        {"nombre": clip.text(item.get("label")) or SIN_INFORMACION, "cantidad": item.get("count")}
        for item in clip.items(values)
        if isinstance(item, Mapping)
    ]


def _counts(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, Mapping) else {}
    return {
        "total": data.get("total"),
        "criticas": data.get("critical"),
        "altas": data.get("high"),
        "medias": data.get("medium"),
        "bajas": data.get("low"),
        "abiertas": data.get("open"),
        "reconocidas": data.get("acknowledged"),
        "pospuestas": data.get("snoozed"),
        "asignadas": data.get("assigned"),
        "falsos_positivos": data.get("false_positive"),
    }


def _financial(value: Any, clip: _Clip) -> dict[str, Any]:
    data = value if isinstance(value, Mapping) else {}
    return {
        "ingresos_usd": data.get("revenue_usd"),
        "facturado_usd": data.get("billed_usd"),
        "trabajo_en_proceso_usd": data.get("wip_usd"),
        "costo_usd": data.get("cost_usd"),
        "margen_usd": data.get("margin_usd"),
        "margen_pct": data.get("margin_pct"),
        "ventas": data.get("sales_revenue"),
        "cartera_pendiente": data.get("backlog_value"),
        "pedidos_abiertos": data.get("open_orders"),
        "dias_cartera_mas_antigua": data.get("oldest_backlog_days"),
        "gasto_de_compras": data.get("purchase_spend"),
        "proyectos_en_riesgo": [
            {
                "nombre": clip.text(item.get("label")) or SIN_INFORMACION,
                "margen_pct": item.get("margin_pct"),
                "trabajo_en_proceso_usd": item.get("wip_usd"),
                "margen_usd": item.get("margin_usd"),
            }
            for item in clip.items(data.get("risk_projects"))
            if isinstance(item, Mapping)
        ],
    }


async def consultar_control_room_resumen(user: dict[str, Any], args: BaseModel) -> ActionResult:
    _args(catalog.ConsultarControlRoomResumenArgs, args)
    payload = await adapters.control_room_summary(user)
    clip = _Clip()
    lessons = payload.get("lessons") if isinstance(payload.get("lessons"), Mapping) else {}
    thresholds = payload.get("thresholds") if isinstance(payload.get("thresholds"), Mapping) else {}
    datos = {
        "anomalias_totales": payload.get("total_anomalies"),
        "por_severidad": _counts(payload.get("by_severity")),
        "por_capacidad": _breakdown(payload.get("by_cartridge"), clip),
        "por_dominio": _breakdown(payload.get("by_domain"), clip),
        "decisiones_abiertas": payload.get("open_decisions"),
        "fuentes_con_datos": _breakdown(payload.get("sources"), clip),
        "finanzas": _financial(payload.get("financial"), clip),
        "umbrales": {"activos": thresholds.get("active"), "total": thresholds.get("total")},
        "aprendizajes": {
            "total": lessons.get("total"),
            "por_capacidad": _breakdown(lessons.get("by_capability"), clip),
        },
        "alertas": _counts(payload.get("alerts")),
    }
    return ActionResult(
        resumen=(
            f"{payload.get('total_anomalies') or 0} anomalías y "
            f"{payload.get('open_decisions') or 0} decisiones abiertas en el Control Room."
        ),
        datos=datos,
        truncado=clip.truncated,
    )


def _certification_coverage(confianza: Mapping[str, Any]) -> dict[str, Any]:
    coverage = confianza.get("cobertura_certificaciones")
    base = {
        "alcance": "global_sin_segmentar",
        "etiqueta": "Cobertura de certificaciones (global, sin segmentar)",
    }
    if not isinstance(coverage, Mapping) or coverage.get("coverage_pct") is None:
        return {
            **base,
            "estado": "sin_informacion",
            "mensaje": "Sin información (requiere Aprendizaje conectado)",
        }
    return {
        **base,
        "estado": "disponible",
        "porcentaje": coverage.get("coverage_pct"),
        "eventos_completados": coverage.get("completed_events"),
        "eventos_de_aprendizaje": coverage.get("learning_events"),
    }


def _nine_box_cells(values: Any, clip: _Clip) -> list[dict[str, Any]]:
    return [
        {
            "cuadrante": clip.text(cell.get("box_label")) or SIN_INFORMACION,
            "potencial": cell.get("potential_band"),
            "desempeno": cell.get("performance_band"),
            "accion_sugerida": clip.text(cell.get("movement_action")),
            "orden": cell.get("display_order"),
            "empleados": cell.get("employee_count"),
            "listos": cell.get("ready_count"),
            "con_evaluacion_real": cell.get("cpa_real_count"),
            "de_referencia": cell.get("reference_count"),
            "deducidos": cell.get("deduced_count"),
            "bloqueados": cell.get("blocked_count"),
            "estado": cell.get("status"),
        }
        for cell in clip.items(values, 9)
        if isinstance(cell, Mapping)
    ]


def _confianza_panel(confianza: Mapping[str, Any], clip: _Clip) -> dict[str, Any]:
    stars = confianza.get("estrellas_en_riesgo")
    vacancies = confianza.get("vacantes_criticas_sin_sucesor")
    exposure = confianza.get("exposicion_monetaria")
    return {
        "estrellas_en_riesgo": stars.get("count") if isinstance(stars, Mapping) else None,
        "vacantes_criticas_sin_sucesor": (
            {
                "cantidad": vacancies.get("count"),
                "puestos": [
                    clip.text(role) for role in clip.items(vacancies.get("roles")) if isinstance(role, str)
                ],
            }
            if isinstance(vacancies, Mapping)
            else None
        ),
        "cobertura_certificaciones": _certification_coverage(confianza),
        "exposicion_monetaria": [
            {
                "banda_de_riesgo": item.get("risk_band"),
                "moneda": item.get("currency"),
                "personas": item.get("headcount"),
                "compensacion_anual_total": item.get("annualized_comp_total"),
                "compensacion_anual_promedio": item.get("annualized_comp_avg"),
            }
            for item in clip.items((exposure or {}).get("totals") if isinstance(exposure, Mapping) else [])
            if isinstance(item, Mapping)
        ],
    }


async def consultar_matriz_talento_9box(user: dict[str, Any], args: BaseModel) -> ActionResult:
    parsed = _args(catalog.ConsultarMatrizTalento9boxArgs, args)
    payload = await adapters.talent_nine_box(user)
    confianza = payload.get("confianza") if isinstance(payload.get("confianza"), Mapping) else {}
    clip = _Clip()
    if parsed.collar == "sindicalizado":
        return ActionResult(
            resumen=(
                "El segmento sindicalizado está en espera de conexión; solo hay cobertura "
                "global de certificaciones."
            ),
            datos={
                "segmento": "sindicalizado",
                "estado": "en_espera_de_conexion",
                "mensaje": (
                    "Este segmento no reutiliza la matriz de confianza. Faltan fuentes por conectar."
                ),
                "fuentes_requeridas": list(UNIONIZED_SOURCES),
                "cobertura_certificaciones": _certification_coverage(confianza),
            },
        )
    totals = payload.get("totals") if isinstance(payload.get("totals"), Mapping) else {}
    cohort = payload.get("desempeno_disponible")
    band_counts = (cohort or {}).get("band_counts") if isinstance(cohort, Mapping) else None
    datos = {
        "segmento": "confianza",
        "estado": payload.get("status") or SIN_INFORMACION,
        "generado_en": payload.get("generated_at"),
        "totales": {
            "empleados": totals.get("employees"),
            "listos": totals.get("ready"),
            "de_referencia": totals.get("reference"),
            "deducidos": totals.get("deduced"),
            "bloqueados": totals.get("blocked"),
            "cuadrantes": totals.get("cells"),
        },
        "cuadrantes": _nine_box_cells(payload.get("cells"), clip),
        "desempeno_disponible": (
            {
                "total": cohort.get("count"),
                "por_banda": {
                    "alto": (band_counts or {}).get("high"),
                    "medio": (band_counts or {}).get("medium"),
                    "bajo": (band_counts or {}).get("low"),
                },
            }
            if isinstance(cohort, Mapping)
            else None
        ),
        "indicadores_confianza": _confianza_panel(confianza, clip),
        "bloqueos": [
            clip.text(item.get("title")) or SIN_INFORMACION
            for item in clip.items(payload.get("blockers"))
            if isinstance(item, Mapping)
        ],
    }
    return ActionResult(
        resumen=(
            f"Matriz 9-box de confianza con {totals.get('employees') or 0} empleados agregados "
            f"en {len(datos['cuadrantes'])} cuadrantes."
        ),
        datos=datos,
        truncado=clip.truncated,
    )


async def consultar_kpis_sap_b1(user: dict[str, Any], args: BaseModel) -> ActionResult:
    parsed = _args(catalog.ConsultarKpisSapB1Args, args)
    views = await adapters.sap_b1_views(user, area=parsed.area, top_n=parsed.top_n)
    clip = _Clip()
    secciones: dict[str, Any] = {}
    for view, payload in views.items():
        secciones[SAP_B1_VIEW_LABELS.get(view, view)] = _translate(payload, clip)
    estados = sorted(
        {str(section.get("estado")) for section in secciones.values() if isinstance(section, Mapping) and section.get("estado")}
    )
    return ActionResult(
        resumen=f"Indicadores de SAP Business One del área {parsed.area}: {', '.join(estados) or SIN_INFORMACION}.",
        datos={"area": parsed.area, "secciones": secciones},
        truncado=clip.truncated,
    )


async def buscar_documentos_empresa(user: dict[str, Any], args: BaseModel) -> ActionResult:
    parsed = _args(catalog.BuscarDocumentosEmpresaArgs, args)
    payload = await adapters.document_search(user, query=parsed.consulta, top_k=parsed.max_resultados)
    clip = _Clip()
    results = []
    for item in clip.items(payload.get("results"), parsed.max_resultados):
        if not isinstance(item, Mapping):
            continue
        snippet = item.get("context") or item.get("child_content")
        similarity = item.get("similarity")
        results.append(
            {
                "documento": clip.text(item.get("source_name"), 200) or SIN_INFORMACION,
                "fragmento": clip.text(snippet, MAX_SNIPPET_CHARS) or "",
                "relevancia": round(float(similarity), 3) if isinstance(similarity, (int, float)) else None,
            }
        )
    return ActionResult(
        resumen=f"{len(results)} fragmentos encontrados en los documentos de la empresa.",
        datos={"resultados": results},
        truncado=clip.truncated,
    )


async def listar_tablas_disponibles(user: dict[str, Any], args: BaseModel) -> ActionResult:
    parsed = _args(catalog.ListarTablasDisponiblesArgs, args)
    source_id = sources.resolve_source(parsed.fuente, user) if parsed.fuente.strip() else None
    payload = await adapters.published_datasets(user)
    items = [item for item in payload.get("datasets") or [] if isinstance(item, Mapping)]
    if parsed.capa != "todas":
        items = [item for item in items if str(item.get("layer") or "") == parsed.capa]
    else:
        items = [item for item in items if str(item.get("layer") or "") in LAYER_LABELS]
    if source_id:
        items = [item for item in items if str(item.get("cartridge") or "") == source_id]
    clip = _Clip()
    tablas = [
        {
            "nombre": str(item.get("name") or ""),
            "capa": LAYER_LABELS.get(str(item.get("layer") or ""), SIN_INFORMACION),
            "fuente": sources.source_label(str(item.get("cartridge") or "")),
            "descripcion": clip.text(item.get("description")) or SIN_INFORMACION,
        }
        for item in clip.items(items, MAX_TABLES)
        if item.get("name")
    ]
    return ActionResult(
        resumen=f"{len(items)} tablas disponibles; se muestran {len(tablas)}.",
        datos={"total": len(items), "tablas": tablas},
        truncado=clip.truncated,
    )


def _sync_errors(values: Any, clip: _Clip) -> list[dict[str, Any]]:
    rows = []
    for item in clip.items(values):
        if not isinstance(item, Mapping):
            continue
        entity = clip.text(item.get("entity"), 80)
        rows.append(
            {
                "entidad": entity or SIN_INFORMACION,
                "mensaje": sync_copy.reason_copy(item.get("reason"))
                or "La extracción de esta entidad no terminó correctamente.",
            }
        )
    return rows


def _sync_payload(payload: Mapping[str, Any], clip: _Clip, *, source_id: str) -> dict[str, Any]:
    status = str(payload.get("status") or "")
    steps = [
        {
            "paso": sync_copy.step_label(step.get("id")),
            "estado": sync_copy.step_status_copy(step.get("status")),
            "avance_pct": step.get("percent"),
        }
        for step in clip.items(payload.get("steps"))
        if isinstance(step, Mapping)
    ]
    return {
        "ejecucion_id": payload.get("run_id"),
        "fuente": sources.source_label(source_id),
        "estado": status or None,
        "estado_texto": sync_copy.status_copy(status),
        "titulo": sync_copy.card_title(status),
        "activa": bool(payload.get("active")),
        "avance_pct": payload.get("progress_percent"),
        "pasos": steps,
        "entidades_iniciadas": len(payload.get("triggered_entities") or []),
        "errores": _sync_errors(payload.get("errors"), clip),
        "iniciada_en": payload.get("started_at"),
        "terminada_en": payload.get("finished_at"),
    }


async def consultar_extraccion(user: dict[str, Any], args: BaseModel) -> ActionResult:
    parsed = _args(catalog.ConsultarExtraccionArgs, args)
    source_id = sources.resolve_source(parsed.fuente, user)
    payload = await adapters.sync_run(user, cartridge=source_id, run_id=parsed.ejecucion_id)
    clip = _Clip()
    datos = _sync_payload(payload, clip, source_id=source_id)
    return ActionResult(
        resumen=f"{datos['titulo']}: {datos['estado_texto']} ({datos['avance_pct'] or 0}%).",
        datos=datos,
        truncado=clip.truncated,
    )


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _started_before(value: Any, moment: datetime) -> bool:
    started = _as_utc(value)
    return started is not None and started < moment - REUSE_CLOCK_SKEW


async def ejecutar_extraccion(user: dict[str, Any], args: BaseModel) -> ActionResult:
    parsed = _args(catalog.EjecutarExtraccionArgs, args)
    source_id = sources.resolve_source(parsed.fuente, user)
    key = parsed.clave_idempotencia or uuid.uuid4().hex
    request_id = f"ia-{key}"
    requested_at = datetime.now(UTC)
    payload = await adapters.start_sync(user, cartridge=source_id, request_id=request_id)
    run_id = str(payload.get("run_id") or "")
    already_running = False
    reused = False
    if run_id:
        origin = await adapters.sync_run_origin(user, cartridge=source_id, run_id=run_id) or {}
        stored = origin.get("request_id")
        already_running = stored is not None and stored != request_id
        reused = (
            bool(parsed.clave_idempotencia)
            and stored == request_id
            and _started_before(origin.get("started_at") or payload.get("started_at"), requested_at)
        )
    clip = _Clip()
    datos = {
        **_sync_payload(payload, clip, source_id=source_id),
        "ya_en_curso": already_running,
        "reutilizada": reused,
        "clave_idempotencia": key,
        "siguiente_paso": (
            "Consulta el avance con consultar_extraccion usando la misma fuente y ejecucion_id."
        ),
    }
    if already_running:
        resumen = "Ya había una extracción en curso para esta fuente; se informa esa ejecución."
    elif reused:
        resumen = (
            "Esta solicitud ya se había registrado con la misma clave de idempotencia; "
            f"no se inició otra extracción. Estado actual: {datos['estado_texto']}."
        )
    else:
        resumen = f"Extracción incremental iniciada: {datos['estado_texto']}."
    return ActionResult(resumen=resumen, datos=datos, truncado=clip.truncated)


APP_NAME_BUSY = (
    "Ya se está publicando una aplicación con ese nombre en este espacio de trabajo; "
    "espera a que termine y consulta si quedó publicada antes de reintentar."
)


async def crear_app_analitica(user: dict[str, Any], args: BaseModel) -> ActionResult:
    parsed = _args(catalog.CrearAppAnaliticaArgs, args)
    base = adapters.public_base_url()
    async with adapters.app_name_lock(user, name=parsed.nombre) as acquired:
        if not acquired:
            raise GatewayError(409, "operacion_en_curso", APP_NAME_BUSY)
        if await adapters.app_name_in_use(user, name=parsed.nombre):
            path = adapters.app_viewer_path(parsed.nombre)
            raise GatewayError(
                409,
                "nombre_en_uso",
                "Ya existe una aplicación con ese nombre en este espacio de trabajo; elige otro nombre.",
                datos={"nombre": parsed.nombre, "enlace": f"{base}{path}" if base else path},
            )
        result = await adapters.create_analytic_app(
            user,
            name=parsed.nombre,
            objective=parsed.objetivo,
            datasets=list(parsed.tablas),
            description=parsed.descripcion,
        )
    path = adapters.app_viewer_path(str(result.get("name") or parsed.nombre))
    return ActionResult(
        resumen=f"Aplicación «{result.get('title') or parsed.nombre}» publicada.",
        datos={
            "nombre": result.get("name") or parsed.nombre,
            "titulo": result.get("title"),
            "enlace": f"{base}{path}" if base else path,
            "tablas": list(result.get("datasets") or parsed.tablas),
        },
    )


Handler = Callable[[dict[str, Any], BaseModel], Awaitable[ActionResult]]
HANDLERS: dict[str, Handler] = {
    "consultar_contexto": consultar_contexto,
    "consultar_salud_pipelines": consultar_salud_pipelines,
    "consultar_control_room_resumen": consultar_control_room_resumen,
    "consultar_matriz_talento_9box": consultar_matriz_talento_9box,
    "consultar_kpis_sap_b1": consultar_kpis_sap_b1,
    "buscar_documentos_empresa": buscar_documentos_empresa,
    "listar_tablas_disponibles": listar_tablas_disponibles,
    "consultar_extraccion": consultar_extraccion,
    "ejecutar_extraccion": ejecutar_extraccion,
    "crear_app_analitica": crear_app_analitica,
}


__all__ = ("ActionResult", "HANDLERS", "KEY_COPY")
