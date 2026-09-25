"""
Cartridge Intent + Pattern Memory — Levels 3 & 4.
=================================================
Level 4 (natural language): parse a business request like
"conéctame HubSpot y dame un dashboard de forecast" into a structured
build intent (source name, desired outputs, cross-source joins).

Level 3 (pattern memory): a deterministic, in-process library of reusable
build patterns keyed by source-kind + auth, plus domain-aware analytic
suggestions ("for a CRM you'll want win-rate, pipeline velocity, forecast
at-risk"). The persistent RAG-backed memory plugs into ``recall_fn`` later;
this module is the pure, testable core.
"""
from __future__ import annotations

import copy
import re
from typing import Any

# ── Level 4: natural-language intent parsing ─────────────────────────────────

# Known source aliases -> canonical cartridge id + likely kind.
_SOURCE_ALIASES: dict[str, dict[str, str]] = {
    "hubspot": {"id": "hubspot", "kind": "rest", "domain": "crm"},
    "salesforce": {"id": "salesforce", "kind": "rest", "domain": "crm"},
    "sf": {"id": "salesforce", "kind": "rest", "domain": "crm"},
    "replicon": {"id": "replicon", "kind": "rest", "domain": "psa"},
    "business one": {"id": "sap_b1", "kind": "sql", "domain": "erp"},
    "sap b1": {"id": "sap_b1", "kind": "sql", "domain": "erp"},
    "b1": {"id": "sap_b1", "kind": "sql", "domain": "erp"},
    "sap": {"id": "sap_s4hana", "kind": "odata", "domain": "erp"},
    "s4hana": {"id": "sap_s4hana", "kind": "odata", "domain": "erp"},
    "s/4hana": {"id": "sap_s4hana", "kind": "odata", "domain": "erp"},
    "successfactors": {"id": "sap_successfactors", "kind": "odata", "domain": "hcm"},
    "workday": {"id": "workday", "kind": "rest", "domain": "hcm"},
    "netsuite": {"id": "netsuite", "kind": "soap", "domain": "erp"},
    "stripe": {"id": "stripe", "kind": "rest", "domain": "billing"},
    "zendesk": {"id": "zendesk", "kind": "rest", "domain": "support"},
}

# Alias family grouping: aliases in the same family belong to one vendor and
# must NOT trigger cross_source=True when co-mentioned (e.g. "SAP SuccessFactors").
_ALIAS_FAMILY: dict[str, str] = {
    "sap": "sap",
    "s4hana": "sap",
    "s/4hana": "sap",
    "successfactors": "sap",
    "business one": "sap",
    "sap b1": "sap",
    "b1": "sap",
}

_OUTPUT_HINTS = {
    "dashboard": ("dashboard", "tablero", r"\bpanel\b", "visualiz", "gráfica", "grafica"),
    "forecast": ("forecast", "pronóstico", "pronostico", "proyección", "proyeccion"),
    "report": ("reporte", "report", "informe"),
    "metrics": ("métrica", "metrica", "kpi", "indicador"),
    "agent": ("agente", "vigía", "vigia", "watchdog", "alerta", r"\bmonitor\b"),
}


def parse_build_intent(text: str) -> dict[str, Any]:
    """Turn a free-text request into a structured build intent. Never raises."""
    t = str(text or "").strip()
    low = t.lower()

    # detect source(s) mentioned
    sources: list[dict[str, str]] = []
    for alias, meta in _SOURCE_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", low):
            if meta["id"] not in {s["id"] for s in sources}:
                sources.append({"alias": alias, **meta})

    # Family veto: aliases in the same vendor family (e.g. "SAP" + "SuccessFactors")
    # are de-duplicated so they never inflate the source count to trigger cross_source.
    seen_families: set[str] = set()
    deduped: list[dict[str, str]] = []
    for s in sources:
        family = _ALIAS_FAMILY.get(s["alias"], s["id"])
        if family not in seen_families:
            seen_families.add(family)
            deduped.append(s)
    sources = deduped

    # detect desired outputs (use regex for hints that start with r"\b" prefix)
    outputs = [
        out for out, hints in _OUTPUT_HINTS.items()
        if any(re.search(h, low) if h.startswith(r"\b") else h in low for h in hints)
    ]

    # cross-source join: feasible only with 2+ distinct-family sources.
    intends_cross = bool(re.search(
        r"\b(?:cruzar?|combinar?|join|juntar?|merge|cross[_\- ]?source)\b", low
    ))
    cross_source = len(sources) >= 2

    return {
        "raw": t,
        "sources": sources,
        "primary_source": sources[0] if sources else None,
        "outputs": outputs or ["metrics"],
        "cross_source": cross_source,
        "intends_cross": intends_cross,
        "wants_agent": "agent" in outputs,
        "actionable": bool(sources),
    }


# ── Level 3: pattern memory + domain suggestions ─────────────────────────────

# Reusable build patterns keyed by (kind, auth). A real deployment would back
# this with RAG over previously-built cartridges; the shape is the same.
_BUILD_PATTERNS: dict[str, dict[str, Any]] = {
    "odata:oauth2": {
        "extractor": "odata_paginated_oauth2",
        "pagination": "skiptoken",
        "watermark_param": "$filter modified gt",
        "notes": "OData V2/V4 con OAuth2 client_credentials; pagina por @odata.nextLink.",
    },
    "odata:basic": {
        "extractor": "odata_paginated_basic",
        "pagination": "skiptoken",
        "watermark_param": "$filter",
        "notes": "OData con Basic Auth + sap-client mandante.",
    },
    "soap:basic": {
        "extractor": "soap_wsse_basic",
        "pagination": "none",
        "watermark_param": "lastModifiedDate",
        "notes": "SOAP/WSDL con WS-Security o Basic Auth; operaciones list/get.",
    },
    "rest:bearer": {
        "extractor": "rest_offset_bearer",
        "pagination": "cursor|offset",
        "watermark_param": "modifiedSince",
        "notes": "REST con bearer token; cursor o offset según la API.",
    },
    "rest:api_key": {
        "extractor": "rest_offset_apikey",
        "pagination": "page",
        "watermark_param": "updated_after",
        "notes": "REST con api-key header.",
    },
    "sql:none": {
        "extractor": "sql_information_schema",
        "pagination": "none",
        "watermark_param": "updated_at",
        "notes": "Extracción directa por tabla vía information_schema.",
    },
}

# Domain-aware analytic suggestions — what a savvy analyst would want.
_DOMAIN_ANALYTICS: dict[str, list[dict[str, str]]] = {
    "crm": [
        {"name": "win_rate_by_owner", "desc": "Tasa de cierre por responsable comercial."},
        {"name": "pipeline_velocity", "desc": "Velocidad de avance de etapas del pipeline."},
        {"name": "forecast_at_risk", "desc": "Deals abiertos en riesgo de no cerrar en el periodo."},
    ],
    "psa": [
        {"name": "utilization_by_resource", "desc": "Utilización facturable por recurso."},
        {"name": "project_margin", "desc": "Margen real por proyecto (revenue - costo)."},
        {"name": "unbilled_hours", "desc": "Horas no facturables que erosionan el margen."},
    ],
    "erp": [
        {"name": "ar_aging", "desc": "Antigüedad de cuentas por cobrar (cartera vencida)."},
        {"name": "spend_concentration", "desc": "Concentración de gasto por proveedor/categoría."},
        {"name": "margin_by_product", "desc": "Margen por SKU/línea de producto."},
    ],
    "hcm": [
        {"name": "turnover_by_unit", "desc": "Rotación por unidad organizacional."},
        {"name": "headcount_trend", "desc": "Tendencia de headcount activo."},
        {"name": "comp_vs_market", "desc": "Compensación vs mercado por puesto."},
    ],
    "billing": [
        {"name": "mrr_trend", "desc": "Tendencia de ingreso recurrente mensual."},
        {"name": "churn_rate", "desc": "Tasa de cancelación de clientes."},
    ],
    "support": [
        {"name": "ticket_sla", "desc": "Cumplimiento de SLA de tickets."},
        {"name": "csat_trend", "desc": "Tendencia de satisfacción del cliente."},
    ],
}


def recall_pattern(kind: str, auth: str = "bearer") -> dict[str, Any] | None:
    """Recall a reusable build pattern for a (kind, auth) combo."""
    k = str(kind or "rest").lower()
    a = str(auth or "bearer").lower().replace("bearer_token", "bearer")
    key = f"{k}:{a}"
    if key in _BUILD_PATTERNS:
        return {"key": key, **_BUILD_PATTERNS[key]}
    # fall back to the kind's default auth (oauth2 before basic so OData+bearer
    # → oauth2 extractor, not basic — basic is a legacy fallback).
    for cand in (f"{k}:bearer", f"{k}:oauth2", f"{k}:basic", f"{k}:none", f"{k}:api_key"):
        if cand in _BUILD_PATTERNS:
            return {"key": cand, **_BUILD_PATTERNS[cand]}
    return None


def suggest_analytics(domain: str) -> list[dict[str, str]]:
    """Domain-aware proactive analytic suggestions.

    Deep-copies so a caller mutating a suggestion can never corrupt the
    module-global ``_DOMAIN_ANALYTICS`` catalog for other callers.
    """
    return copy.deepcopy(_DOMAIN_ANALYTICS.get(str(domain or "").lower(), []))


def learn_from_correction(memory: dict[str, Any], pattern_key: str, corrected_sql: str) -> dict[str, Any]:
    """Record a human SQL correction as a learned override for a pattern.

    Deep-copies the incoming memory so the returned dict shares no nested
    state with the caller's original snapshot (true immutability).
    """
    mem = copy.deepcopy(memory) if isinstance(memory, dict) else {}
    learned = dict(mem.get("learned_sql") or {})
    learned[pattern_key] = corrected_sql
    mem["learned_sql"] = learned
    return mem


def recall_learned_sql(memory: dict[str, Any], pattern_key: str) -> str | None:
    # ``learned_sql`` may be absent OR explicitly None (after a store round-trip
    # that nulls empty maps) — guard both so this never raises.
    return ((memory if isinstance(memory, dict) else {}).get("learned_sql") or {}).get(pattern_key)
