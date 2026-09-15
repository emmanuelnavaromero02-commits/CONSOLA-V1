"""Domain KPI views (Finance / Operations / Risk) for the Control Room bridge.

Mission 2: each view runs the Mission 1 aggregates of one domain
(``app.services.intelligence.{finance,operations,risk}_aggregates``) and folds
them into one dict the internal read bridge (``POST
/api/control-room/internal/read``) projects through the public response
schemas and hands to the MCP tools ``control_room__{finance,operations,risk}_kpis_read``.

The payload is built for the LLM, so it must survive the public projection
(``control_room_public_projection``), which redacts technical copy: dataset
and column identifiers, tier-qualified table names, UUIDs. Therefore:

* every metric carries a PUBLIC proxy note (plain business Spanish, no
  identifiers) that states what the number measures and what it does NOT;
  the developer-facing ``proxy_note`` of the aggregate result is not exposed;
* ``error`` becomes a stable reason code plus a fixed Spanish phrase, so the
  LLM learns WHY a metric is missing without receiving dataset or column
  identifiers (and without a Postgres error text that would be redacted);
* every developer note is translated to a public equivalent: a degraded metric
  must always say why, so notes are mapped, never silently dropped;
* evidence references expose a business ``source`` label instead of the
  physical relation, plus generation and snapshot timestamp (``run_id``,
  ``dataset`` and column lists are internal and never published);
* cartridge ids are complemented with a business label.

Global status per domain: ``ready`` when every metric is ready, ``unavailable``
when every metric is unavailable, ``degraded`` otherwise.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.services.intelligence import domain_memory_hooks
from app.services.intelligence import (
    finance_aggregates,
    operations_aggregates,
    risk_aggregates,
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

# LLM-facing explanations. Plain business Spanish on purpose: no dataset,
# column or table identifiers, so the public projection keeps them verbatim
# (see tests/test_control_room_domain_kpis.py::test_public_notes_survive_projection).
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
}

PUBLIC_CARTRIDGE_LABELS: dict[str, str] = {
    "sap_successfactors": "SAP SuccessFactors",
    "sap_hcm": "SAP HCM",
    "sap_s4hana": "SAP S/4HANA",
    "replicon": "Replicon",
    "salesforce": "Salesforce",
    "hubspot": "HubSpot",
    "banxico": "Banxico",
    "inegi": "INEGI",
    "sec_edgar": "SEC EDGAR",
}

# Scalar evidence filters that are safe and useful for the LLM.
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
    """ready if all ready; unavailable if all unavailable; degraded otherwise."""
    if not statuses:
        return STATUS_UNAVAILABLE
    if all(status == STATUS_READY for status in statuses):
        return STATUS_READY
    if all(status == STATUS_UNAVAILABLE for status in statuses):
        return STATUS_UNAVAILABLE
    return STATUS_DEGRADED


# Evidence type labels that survive the public projection ("gold_relation"
# would be redacted as a tier-qualified identifier).
PUBLIC_EVIDENCE_TYPES: dict[str, str] = {
    "gold_relation": "published_dataset",
    "console_table": "run_log",
}


def public_text(value: Any) -> str | None:
    """Return the string when the public projection would keep it, else None."""
    if value is None:
        return None
    text = str(value)
    return None if contains_public_technical_copy(text) else text


# Stable reason codes for a metric that could not be computed. The prefix is
# the machine-readable half of the aggregate error (see
# domain_aggregate_support._HTTP_REASONS and invalid_schema_error); the tail is
# a fixed phrase, because the aggregate tail carries dataset/column names or a
# driver error text the public projection would redact.
PUBLIC_ERROR_REASONS: dict[str, str] = {
    "missing": "el origen no esta publicado para este workspace",
    "invalid_schema": "el origen publicado no tiene la forma esperada",
    "no_permission": "sin permiso de lectura o sin workspace activo",
    "invalid_scope": "sin workspace activo",
    "unavailable": "el origen de datos no esta disponible",
}
_DEFAULT_ERROR_REASON = "el origen de datos no esta disponible"

# Developer notes -> public equivalents. Ordered: the first marker contained in
# the note wins. A note with no rule is kept when it is public-safe, and
# otherwise reported as a generic limitation, never dropped in silence.
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
    """Stable reason code + fixed phrase, never the aggregate's technical tail."""
    text = str(error or "").strip()
    if not text:
        return None
    reason = text.split(":", 1)[0].strip()
    if reason not in PUBLIC_ERROR_REASONS:
        return f"unavailable: {_DEFAULT_ERROR_REASON}"
    return f"{reason}: {PUBLIC_ERROR_REASONS[reason]}"


def public_note(note: Any) -> str | None:
    """Translate one developer note into public copy (never silently dropped)."""
    text = str(note or "").strip()
    if not text:
        return None
    for marker, replacement in PUBLIC_NOTE_RULES:
        if marker in text:
            return replacement
    return text if not contains_public_technical_copy(text) else _GENERIC_NOTE


# Filter VALUES are published too, so they need the same treatment as notes:
# the aggregates put explanatory prose in some of them (base_currency carries
# "unverified (<dataset>.<column> is NULL)"), which would hand the model a
# dataset and a column name.
PUBLIC_FILTER_VALUES: dict[str, str] = {
    "unverified (pnl_mensual.base_currency is NULL)": "sin verificar en el origen",
}


def public_filter_value(value: Any) -> Any:
    """Public form of one evidence filter value, or None to drop the key."""
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
    """Translate every note to public copy, preserving order and dropping dups."""
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
                    # The publication run id is a UUID and a forbidden public
                    # key; generation + published_at identify the snapshot.
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
    """Add the business label; keep the raw id only when it is public-safe."""
    return [
        {
            **row,
            "cartridge": cartridge_label(row.get("cartridge_id")),
            "cartridge_id": public_text(row.get("cartridge_id")),
        }
        for row in rows or []
    ]


def _bucket_rows(buckets: dict[str, Any]) -> list[dict[str, Any]]:
    """Public projection models cannot have field names starting with a digit."""
    return [
        {"bucket": name, **(values if isinstance(values, dict) else {})}
        for name, values in (buckets or {}).items()
    ]


def metric_payload(metric: str, result: AggregateResult) -> dict[str, Any]:
    payload = result.to_dict()
    payload["proxy_note"] = PUBLIC_PROXY_NOTES.get(metric)
    payload["error"] = public_error(payload.get("error"))
    # Column allowlists are internal; the public signal is status + notes.
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
        # Second precision on purpose: a microsecond ISO stamp is redacted as
        # technical copy by the public projection.
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
    """Finance KPIs: billable hours, labor cost by department, project margin.

    ``top_n`` (0..10) forwards the controlled named-rows exception to
    ``query_project_margin``; the default 0 keeps everything aggregate-only.
    """
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
    # NOTE Mission 4: the cost-centre budget gap is NOT recorded here. This
    # function is the body of control_room__finance_kpis_read, which is classified
    # read-only and approval-free and gated only on datasets.read, so an INSERT on
    # this path would be a persistent write hiding behind a read classification —
    # reachable by any caller with datasets.read and never covered by the
    # scheduled-effect fence. The write lives on the wisdom-bit path instead
    # (control_room.domain_wisdom_bits), which is already classified as an
    # advisory write and requires control_room.write.
    return domain_payload(FINANCE_DOMAIN, results, named_rows=named_rows)


async def operations_kpis(user: dict | None) -> dict[str, Any]:
    """Operations KPIs: pipeline health, data freshness, absence rate."""
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
    """Risk KPIs: attrition risk population, employment end expiry, deal slippage.

    ``top_n`` (0..10) forwards the controlled named-rows exception to
    ``query_deal_slippage``; the default 0 keeps everything aggregate-only.
    """
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
    # Mission 4. Risk cannot compute cost-centre overrun for the same reason
    # Finance cannot compute budget-vs-actual. Rather than rediscovering it, Risk
    # reads the shared memory and cites whoever recorded it.
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
