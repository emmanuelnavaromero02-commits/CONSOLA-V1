from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.services.intelligence import domain_memory_hooks
from app.services.intelligence import (
    finance_aggregates,
    operations_aggregates,
    risk_aggregates,
    sap_b1_aggregates,
)
from app.services.intelligence.domain_aggregate_support import (
    STATUS_DEGRADED,
    STATUS_READY,
    STATUS_UNAVAILABLE,
    AggregateResult,
    clamp_named_rows,
    jsonable,
)
from app.services.public_text_sensitivity import contains_public_technical_copy

FINANCE_DOMAIN = "finance"
OPERATIONS_DOMAIN = "operations"
RISK_DOMAIN = "risk"

FINANCE_METRICS = (
    "billable_hours_logged",
    "labor_cost_by_department",
    "project_margin",
)
OPERATIONS_METRICS = (
    "pipeline_health",
    "data_freshness_by_cartridge",
    "absence_rate_company_by_type",
)
RISK_METRICS = ("attrition_risk_population", "employment_end_expiry", "deal_slippage")
SAP_B1_MARGIN_METRICS = (
    "margen_bruto",
    "margen_contribucion",
    "destructores",
    "concentracion_top20",
    "margen_vendedor",
    "reconciliacion_finanzas",
    "calidad_datos",
    "modelo_entidades",
)
SAP_B1_SALES_METRICS = ("ratio_sellout_sellin", "dias_inventario", "sellout_clinica", "semaforo_distribuidoras")
SAP_B1_EXPIRY_METRICS = ("caducidad_lotes",)
SAP_B1_SUPPLY_METRICS = ("dias_cobertura", "oc_vs_necesidad", "costo_real_vs_estandar", "lead_time_proveedores")
SAP_B1_LEARNING_METRICS = ("aprendizaje",)
SAP_B1_SEMAFORO_METRICS = (
    "margen_bruto",
    "destructores",
    "reconciliacion_finanzas",
    "semaforo_distribuidoras",
    "caducidad_lotes",
    "dias_cobertura",
    "calidad_datos",
)

PUBLIC_PROXY_NOTES: dict[str, str] = {
    "billable_hours_logged": (
        "Horas marcadas como facturables registradas por los consultores en "
        "Replicon, por proyecto y valoradas a la tarifa efectiva. NO mide horas "
        "aprobadas ni horas pendientes de facturar: el estado de aprobacion y el "
        "enlace con la factura no existen en la capa analitica. El monto es una "
        "estimacion a tarifa, no facturacion emitida."
    ),
    "labor_cost_by_department": (
        "Costo laboral estimado de consultores en Replicon (horas ejecutadas por "
        "tarifa de costo por hora), agrupado por departamento, para el ultimo mes "
        "cerrado con datos. NO es nomina: la nomina de SAP no esta extraida y la "
        "compensacion de SuccessFactors llega cifrada, asi que no se puede sumar."
    ),
    "project_margin": (
        "Margen por proyecto igual a ingreso base menos costo directo y costo "
        "hundido, calculado sobre montos en moneda base sin verificar porque el "
        "origen no resuelve la moneda ni el tipo de cambio. NO es margen sobre "
        "facturacion emitida ni esta convertido a dolares; la facturacion original "
        "se reporta aparte sin conversion."
    ),
    "pipeline_health": (
        "Corridas fallidas y exitosas por cartucho en las ultimas 24 horas y 7 "
        "dias, sumando las dos bitacoras operativas. Los totales cubren todo el "
        "workspace; los desgloses por cartucho y por entidad estan acotados. NO "
        "incluye el texto de los errores ni corridas individuales."
    ),
    "data_freshness_by_cartridge": (
        "Horas transcurridas desde la ultima corrida exitosa de cada cartucho. NO "
        "mide cumplimiento de un acuerdo de servicio: OMEGA no tiene un SLA de "
        "frescura configurado, el umbral es un parametro con valor por defecto de "
        "24 horas. Los cartuchos sin ninguna corrida exitosa visible NO aparecen."
    ),
    "absence_rate_company_by_type": (
        "Tasa de ausentismo a nivel empresa por tipo de ausencia: dias habiles de "
        "ausencia del ultimo mes cerrado divididos entre la plantilla activa actual "
        "por los dias habiles del mes. NO esta desglosada por unidad organizativa "
        "y la plantilla es la actual, no la del mes analizado."
    ),
    "attrition_risk_population": (
        "Empleados por banda de riesgo de salida segun el modelo de Talento "
        "(alto, medio, bajo, datos insuficientes), con desglose por departamento y "
        "la cifra oficial de Talento cuando existe. NO recalcula el riesgo ni usa "
        "datos de compensacion; es solo recomendacion."
    ),
    "employment_end_expiry": (
        "Empleados activos cuya fecha de fin de empleo cae dentro de 30, 60 o 90 "
        "dias, excluyendo el empleo indefinido. Es el fin del registro de empleo en "
        "SuccessFactors, NO un elemento contractual: los contratos de SAP no son "
        "consultables desde la capa analitica."
    ),
    "deal_slippage": (
        "Oportunidades abiertas de Salesforce con fecha de cierre ya vencida: "
        "total, monto, tramos de dias vencidos y desglose por etapa. El desglose "
        "por motivo repite el mismo filtro, asi que NO distingue causas. Los "
        "montos NO estan convertidos de moneda. Los nombres de deals solo "
        "aparecen a peticion explicita y nunca el vendedor."
    ),
    "margen_bruto": sap_b1_aggregates.MARGEN_BRUTO_NOTE,
    "margen_contribucion": sap_b1_aggregates.MARGEN_CONTRIBUCION_NOTE,
    "destructores": sap_b1_aggregates.DESTRUCTORES_NOTE,
    "concentracion_top20": sap_b1_aggregates.CONCENTRACION_NOTE,
    "margen_vendedor": sap_b1_aggregates.MARGEN_VENDEDOR_NOTE,
    "reconciliacion_finanzas": sap_b1_aggregates.RECONCILIACION_NOTE,
    "calidad_datos": sap_b1_aggregates.CALIDAD_NOTE,
    "modelo_entidades": sap_b1_aggregates.MODELO_NOTE,
    "ratio_sellout_sellin": sap_b1_aggregates.RATIO_NOTE,
    "dias_inventario": sap_b1_aggregates.DIAS_INVENTARIO_NOTE,
    "sellout_clinica": sap_b1_aggregates.SELLOUT_CLINICA_NOTE,
    "semaforo_distribuidoras": sap_b1_aggregates.SEMAFORO_DIST_NOTE,
    "caducidad_lotes": sap_b1_aggregates.CADUCIDAD_NOTE,
    "dias_cobertura": sap_b1_aggregates.COBERTURA_NOTE,
    "oc_vs_necesidad": sap_b1_aggregates.OC_NECESIDAD_NOTE,
    "costo_real_vs_estandar": sap_b1_aggregates.COSTO_NOTE,
    "lead_time_proveedores": sap_b1_aggregates.LEAD_TIME_NOTE,
    "aprendizaje": sap_b1_aggregates.APRENDIZAJE_NOTE,
}

PUBLIC_SOURCE_LABELS: dict[str, str] = {
    finance_aggregates.CONSULTOR_MENSUAL_DATASET: "Replicon: horas por consultor y proyecto (mensual)",
    finance_aggregates.COSTO_CONSULTOR_DATASET: "Replicon: costo por consultor (mensual)",
    finance_aggregates.PNL_MENSUAL_DATASET: "Replicon: resultados por proyecto (mensual)",
    operations_aggregates.ABSENCE_DATASET: "SAP HCM: ausencias por tipo y mes",
    operations_aggregates.HEADCOUNT_DATASET: "SAP HCM: plantilla por departamento",
    operations_aggregates.PIPELINE_RUNS_TABLE: "Bitacora de corridas de pipelines",
    operations_aggregates.EXTRACTION_RUNS_TABLE: "Bitacora de extracciones",
    risk_aggregates.RETENTION_RISK_DATASET: "Talento: riesgo de retencion por empleado",
    risk_aggregates.ACTION_CANDIDATES_DATASET: "Talento: acciones candidatas",
    risk_aggregates.EMPLOYEE_360_DATASET: "Talento: ficha de empleado",
    risk_aggregates.DEALS_AT_RISK_DATASET: "Salesforce: deals en riesgo",
    sap_b1_aggregates.KPIS_DATASET: "SAP Business One: indicadores de margen (mensual)",
    sap_b1_aggregates.CONSOLIDATED_DATASET: "SAP Business One: margen del grupo (mensual)",
    sap_b1_aggregates.KPI_RECONCILIATION_DATASET: "SAP Business One: corrida de Finanzas contra la plataforma",
    sap_b1_aggregates.LEDGER_DATASET: "SAP Business One: documentos contra contabilidad (mensual)",
    sap_b1_aggregates.DATA_QUALITY_DATASET: "SAP Business One: calidad de datos",
    sap_b1_aggregates.ENTITY_MODEL_DATASET: "SAP Business One: modelo de entidades",
    sap_b1_aggregates.SCORECARD_DATASET: "SAP Business One: semáforo de distribuidoras (mensual)",
    sap_b1_aggregates.SELLOUT_CLINIC_DATASET: "SAP Business One: sell-out por clínica (mensual)",
    sap_b1_aggregates.EXPIRY_DATASET: "SAP Business One: caducidad de lotes",
    sap_b1_aggregates.COVERAGE_DATASET: "SAP Business One: cobertura y reabasto",
    sap_b1_aggregates.COST_VARIANCE_DATASET: "SAP Business One: costo real contra estándar (mensual)",
    sap_b1_aggregates.LEAD_TIME_DATASET: "SAP Business One: entregas de proveedores (mensual)",
}

PUBLIC_CARTRIDGE_LABELS: dict[str, str] = {
    "sap_successfactors": "SAP SuccessFactors",
    "sap_hcm": "SAP HCM",
    "sap_s4hana": "SAP S/4HANA",
    "sap_b1": "SAP Business One",
    "replicon": "Replicon",
    "salesforce": "Salesforce",
    "hubspot": "HubSpot",
    "banxico": "Banxico",
    "inegi": "INEGI",
    "sec_edgar": "SEC EDGAR",
}

_EVIDENCE_FILTER_KEYS = (
    "window_start",
    "window_end_exclusive",
    "months",
    "top_n",
    "before_month",
    "period",
    "as_of",
    "sla_hours",
    "sla_source",
    "sla_threshold",
    "windows_days",
    "sentinel_excluded_from",
    "active_only",
    "snapshot",
    "status",
    "breakdown_limit",
    "failing_entities_top_n",
    "base_currency",
    "original_currencies",
)


def combine_status(statuses: list[str]) -> str:
    if not statuses:
        return STATUS_UNAVAILABLE
    if all(status == STATUS_READY for status in statuses):
        return STATUS_READY
    if all(status == STATUS_UNAVAILABLE for status in statuses):
        return STATUS_UNAVAILABLE
    return STATUS_DEGRADED


PUBLIC_EVIDENCE_TYPES: dict[str, str] = {
    "gold_relation": "published_dataset",
    "console_table": "run_log",
}


def public_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return None if contains_public_technical_copy(text) else text


PUBLIC_ERROR_REASONS: dict[str, str] = {
    "missing": "el origen no esta publicado para este workspace",
    "invalid_schema": "el origen publicado no tiene la forma esperada",
    "no_permission": "sin permiso de lectura o sin workspace activo",
    "invalid_scope": "sin workspace activo",
    "unavailable": "el origen de datos no esta disponible",
}
_DEFAULT_ERROR_REASON = "el origen de datos no esta disponible"

PUBLIC_NOTE_RULES: tuple[tuple[str, str], ...] = (
    (
        "billing_rate_usd ausente",
        "sin tarifa publicada: no se puede calcular el monto facturable",
    ),
    (
        "headcount_by_department sin filas",
        "la plantilla no tiene filas para este workspace: no se puede calcular "
        "la tasa, solo los dias de ausencia",
    ),
    (
        "headcount_by_department no disponible",
        "la plantilla no esta disponible: no se puede calcular la tasa, solo "
        "los dias de ausencia",
    ),
    (
        "talent_action_candidates no emite",
        "Talento reporta cero empleados en riesgo alto",
    ),
    (
        "talent_action_candidates no disponible",
        "sin cifra oficial de Talento: el catalogo de acciones no esta publicado",
    ),
    ("department_name ausente", "sin desglose por departamento"),
    ("stage_name ausente", "sin desglose por etapa"),
    ("motivo_riesgo ausente", "sin desglose por motivo de riesgo"),
    (
        "amount ausente",
        "sin monto publicado: los desgloses se ordenan por numero de deals",
    ),
    (
        "is_active ausente",
        "sin marca de empleado activo: se cuentan todos los registros con fin "
        "de empleo futuro",
    ),
    ("sin meses cerrados con datos", "sin meses cerrados con datos"),
    (
        "finanzas todavia no entrego totales de control",
        "finanzas todavia no entrego totales de control",
    ),
    ("sin lotes con existencia", "sin lotes con existencia"),
    (
        "hay varias monedas locales",
        "hay varias monedas locales: se reporta la de mayor venta",
    ),
    (
        "retention_risk_score",
        "las bandas de riesgo las calcula el modelo de Talento; aqui no se "
        "recalculan",
    ),
    (
        "recommendation_only",
        "solo recomendacion: riesgo de salida sin datos de compensacion",
    ),
    (
        "ya excluye oportunidades cerradas",
        "el origen ya excluye oportunidades cerradas; el corte corresponde a la "
        "ultima publicacion",
    ),
    (
        "sin conversion de moneda",
        "los montos vienen de Salesforce sin conversion de moneda",
    ),
    (
        "deals nombrados a peticion explicita",
        "la lista de deals nombrados se devuelve solo a peticion explicita",
    ),
)
_GENERIC_NOTE = "el detalle de esta limitacion es interno; ver status y proxy_note"


def public_error(error: Any) -> str | None:
    text = str(error or "").strip()
    if not text:
        return None
    reason = text.split(":", 1)[0].strip()
    if reason not in PUBLIC_ERROR_REASONS:
        return f"unavailable: {_DEFAULT_ERROR_REASON}"
    return f"{reason}: {PUBLIC_ERROR_REASONS[reason]}"


def public_note(note: Any) -> str | None:
    text = str(note or "").strip()
    if not text:
        return None
    for marker, replacement in PUBLIC_NOTE_RULES:
        if marker in text:
            return replacement
    return text if not contains_public_technical_copy(text) else _GENERIC_NOTE


PUBLIC_FILTER_VALUES: dict[str, str] = {
    "unverified (pnl_mensual.base_currency is NULL)": "sin verificar en el origen",
}


def public_filter_value(value: Any) -> Any:
    if isinstance(value, str):
        mapped = PUBLIC_FILTER_VALUES.get(value)
        if mapped is not None:
            return mapped
        return None if contains_public_technical_copy(value) else value
    if isinstance(value, (list, tuple)):
        items = [
            item
            for item in value
            if not (isinstance(item, str) and contains_public_technical_copy(item))
        ]
        return items
    return value


def public_source_label(ref: dict[str, Any]) -> str | None:
    key = ref.get("dataset") or ref.get("table")
    if key is None:
        return None
    return PUBLIC_SOURCE_LABELS.get(str(key), str(key))


def cartridge_label(cartridge_id: Any) -> str | None:
    if cartridge_id is None:
        return None
    return PUBLIC_CARTRIDGE_LABELS.get(str(cartridge_id), str(cartridge_id))


def public_notes(notes: list[Any]) -> list[str]:
    out: list[str] = []
    for note in notes or []:
        text = public_note(note)
        if text and text not in out:
            out.append(text)
    return out


def public_evidence(metric: str, refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ref in refs or []:
        filters = ref.get("filters") if isinstance(ref.get("filters"), dict) else {}
        out.append(
            jsonable(
                {
                    "metric": metric,
                    "type": PUBLIC_EVIDENCE_TYPES.get(
                        str(ref.get("type")), public_text(ref.get("type"))
                    ),
                    "source": public_source_label(ref),
                    "generation": ref.get("generation"),
                    "published_at": ref.get("published_at"),
                    "partial_source": bool(ref.get("missing_optional_columns")),
                    "filters": {
                        key: public
                        for key in _EVIDENCE_FILTER_KEYS
                        if key in filters
                        and (public := public_filter_value(filters[key])) is not None
                    },
                }
            )
        )
    return out


def _label_cartridge_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **row,
            "cartridge": cartridge_label(row.get("cartridge_id")),
            "cartridge_id": public_text(row.get("cartridge_id")),
        }
        for row in rows or []
    ]


def _bucket_rows(buckets: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"bucket": name, **(values if isinstance(values, dict) else {})}
        for name, values in (buckets or {}).items()
    ]


def metric_payload(metric: str, result: AggregateResult) -> dict[str, Any]:
    payload = result.to_dict()
    payload["proxy_note"] = PUBLIC_PROXY_NOTES.get(metric)
    payload["error"] = public_error(payload.get("error"))
    payload.pop("missing_columns", None)
    payload["notes"] = public_notes(payload.get("notes") or [])
    payload["evidence_refs"] = public_evidence(
        metric, payload.get("evidence_refs") or []
    )
    if metric in ("pipeline_health", "data_freshness_by_cartridge"):
        payload["cartridges"] = _label_cartridge_rows(payload.get("cartridges") or [])
    if metric == "pipeline_health":
        payload["failing_entities"] = _label_cartridge_rows(
            payload.get("failing_entities") or []
        )
    if metric == "deal_slippage":
        payload["buckets"] = _bucket_rows(payload.get("buckets") or {})
    return payload


def domain_payload(
    domain: str,
    results: dict[str, AggregateResult],
    *,
    named_rows: int = 0,
) -> dict[str, Any]:
    metrics = {name: metric_payload(name, result) for name, result in results.items()}
    statuses = [result.status for result in results.values()]
    return {
        "domain": domain,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "status": combine_status(statuses),
        "named_rows": named_rows,
        "metrics": metrics,
        "evidence_refs": [
            ref for item in metrics.values() for ref in item["evidence_refs"]
        ],
        "notes": [
            f"{name}: {note}"
            for name, item in metrics.items()
            for note in item["notes"]
        ],
        "unavailable_metrics": [
            name
            for name, result in results.items()
            if result.status == STATUS_UNAVAILABLE
        ],
        "degraded_metrics": [
            name for name, result in results.items() if result.status == STATUS_DEGRADED
        ],
    }


async def finance_kpis(user: dict | None, *, top_n: int = 0) -> dict[str, Any]:
    named_rows = clamp_named_rows(top_n)
    results: dict[str, AggregateResult] = {
        "billable_hours_logged": await finance_aggregates.query_billable_hours_logged(
            user, top_n=named_rows
        ),
        "labor_cost_by_department": await finance_aggregates.query_labor_cost_by_department(
            user
        ),
        "project_margin": await finance_aggregates.query_project_margin(
            user, top_n=named_rows
        ),
    }
    return domain_payload(FINANCE_DOMAIN, results, named_rows=named_rows)


async def operations_kpis(user: dict | None) -> dict[str, Any]:
    results: dict[str, AggregateResult] = {
        "pipeline_health": await operations_aggregates.query_pipeline_health(user),
        "data_freshness_by_cartridge": (
            await operations_aggregates.query_data_freshness_by_cartridge(user)
        ),
        "absence_rate_company_by_type": (
            await operations_aggregates.query_absence_rate_company_by_type(user)
        ),
    }
    return domain_payload(OPERATIONS_DOMAIN, results)


async def risk_kpis(user: dict | None, *, top_n: int = 0) -> dict[str, Any]:
    named_rows = clamp_named_rows(top_n)
    results: dict[str, AggregateResult] = {
        "attrition_risk_population": await risk_aggregates.query_attrition_risk_population(
            user
        ),
        "employment_end_expiry": await risk_aggregates.query_employment_end_expiry(
            user
        ),
        "deal_slippage": await risk_aggregates.query_deal_slippage(
            user, top_n=named_rows
        ),
    }
    payload = domain_payload(RISK_DOMAIN, results, named_rows=named_rows)
    note = await domain_memory_hooks.cost_center_overrun_note(user)
    if note:
        payload["notes"] = [*payload["notes"], note]
    return payload


__all__ = [
    "FINANCE_DOMAIN",
    "FINANCE_METRICS",
    "OPERATIONS_DOMAIN",
    "OPERATIONS_METRICS",
    "PUBLIC_CARTRIDGE_LABELS",
    "PUBLIC_PROXY_NOTES",
    "PUBLIC_SOURCE_LABELS",
    "RISK_DOMAIN",
    "RISK_METRICS",
    "SAP_B1_EXPIRY_METRICS",
    "SAP_B1_LEARNING_METRICS",
    "SAP_B1_MARGIN_METRICS",
    "SAP_B1_SALES_METRICS",
    "SAP_B1_SEMAFORO_METRICS",
    "SAP_B1_SUPPLY_METRICS",
    "cartridge_label",
    "combine_status",
    "domain_payload",
    "finance_kpis",
    "metric_payload",
    "operations_kpis",
    "public_evidence",
    "public_error",
    "public_filter_value",
    "public_note",
    "public_notes",
    "public_source_label",
    "public_text",
    "risk_kpis",
]
