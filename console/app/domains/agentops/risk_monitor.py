"""Risk AgentOps monitor contract (Centinela de Deals).

Watches the three Mission 1 Risk aggregates through the Mission 2 view
``control_room__risk_kpis_read``: attrition-risk population, employment-end
expiry, and deal slippage.

Note what this monitor deliberately does not do. ``risk_kpis`` never returns the
seller behind a slipping deal, and the attrition metric reuses the Talent
retention bands instead of recomputing a risk score. Both are Mission 1
decisions, and the prompt repeats them so the monitor cannot over-promise.
"""

from __future__ import annotations

from typing import Any

from app.domains.agentops.domain_monitor_support import (
    DomainMonitorSpec,
    build_contract,
    is_domain_monitor_row,
    needs_runtime_repair,
)

RISK_MONITOR_CARTRIDGE = "salesforce"
RISK_MONITOR_SLUG = "salesforce_deal_risk_sentinel_monitor"
RISK_WISDOM_BIT_ID = "WB-DEALS"

# cost_center_budget is read, not written, by Risk: Finance records that gap and
# Risk consults it before trying to explain why cost_center_overrun is missing.
RISK_MEMORY_SUBJECTS = (
    "cost_center_budget",
    "salesforce_deals_en_riesgo.motivo_riesgo",
    "employment_end_sentinel_date",
)

RISK_INSTRUCTIONS = """Eres el monitor programado de Riesgo para Control Room.

## Objetivo
Revisar, de forma segura y auditable, el riesgo agregado: poblacion en riesgo de rotacion, fin de contrato proximo y deals que se estan cayendo. No escribes en Salesforce ni en SuccessFactors, no apruebas nada y no expones datos de personas.

## Que vigilas
1. Deals abiertos con cierre vencido y su monto agregado por etapa y por motivo.
2. Poblacion en banda de riesgo alto de rotacion.
3. Empleados con fin de contrato dentro de 30, 60 y 90 dias.

## Cuando alertas
Alerta cuando el estado del dominio no es `ready` y hay al menos una senal concreta. Si el dominio esta `unavailable` NO alertas: sin evidencia no hay riesgo demostrable.

## Que evidencia citas
Solo agregados: conteos por banda, montos por etapa, dias vencidos maximos. NUNCA el vendedor de un deal, nunca un nombre de empleado, nunca un identificador de persona.

## Honestidad obligatoria
- La rotacion reutiliza las bandas de riesgo de Talento; no la recalculas ni la mejoras.
- El fin de contrato excluye la fecha centinela de 2030, que no es un vencimiento real.
- El desglose por motivo de riesgo repite el mismo filtro de la consulta y no distingue causas independientes.
- `cost_center_overrun` no existe porque no hay presupuesto por centro de costo en ningun cartucho. Antes de reportarlo, consulta la memoria compartida: Finanzas ya registro ese hallazgo y debes citarlo en lugar de repetirlo como nuevo.

## Regla de seguridad
Todas las salidas son recommendation_only. No hay write-back externo ni acciones destructivas."""

RISK_MONITOR_SPEC = DomainMonitorSpec(
    key="risk",
    cartridge_id=RISK_MONITOR_CARTRIDGE,
    slug=RISK_MONITOR_SLUG,
    name="Centinela de Deals Monitor",
    domain="Riesgo",
    wisdom_bit_id=RISK_WISDOM_BIT_ID,
    kpi_tool="control_room__risk_kpis_read",
    source_label="risk_kpis",
    description=(
        "Monitor programado de Riesgo: revisa deals vencidos, poblacion en riesgo "
        "de rotacion y fin de contrato proximo, y publica evidencia advisory en "
        "Control Room."
    ),
    instructions=RISK_INSTRUCTIONS,
    # Reused verbatim from the conversational Centinela de Deals seed
    # (infra/init/94_salesforce_seed.sql).
    personality=(
        "Directo y proactivo. Lista priorizada por monto con dueño y motivo. "
        "Idioma del usuario."
    ),
    recommended_action=(
        "Revisar los deals con cierre vencido por monto y los vencimientos de "
        "contrato dentro de 30 dias antes del siguiente ciclo."
    ),
    cron="12,27,42,57 * * * *",
    calibration_group="salesforce:deal_slippage",
    monte_carlo_seed=45230,
    decision_title="Decision operativa WB-DEALS",
    decision_description=(
        "Evaluar si las senales agregadas de deals vencidos y rotacion requieren "
        "abrir investigacion con seguimiento supervisado."
    ),
    risk_metric="deal_slippage_delta",
    rag_cartridges=("salesforce",),
    # Deal closings move with macro conditions, so Banxico/INEGI context applies.
    uses_market_context=True,
    memory_subjects=RISK_MEMORY_SUBJECTS,
)


def risk_monitor_contract() -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    """``(allowed_tools, rag_filter, extra)`` for the Risk monitor."""
    return build_contract(RISK_MONITOR_SPEC)


def is_risk_monitor_row(agent: Any) -> bool:
    return is_domain_monitor_row(agent, RISK_MONITOR_SPEC)


def risk_monitor_needs_runtime_repair(agent: Any) -> bool:
    return needs_runtime_repair(agent, RISK_MONITOR_SPEC)


__all__ = (
    "RISK_MEMORY_SUBJECTS",
    "RISK_MONITOR_CARTRIDGE",
    "RISK_MONITOR_SLUG",
    "RISK_MONITOR_SPEC",
    "RISK_WISDOM_BIT_ID",
    "is_risk_monitor_row",
    "risk_monitor_contract",
    "risk_monitor_needs_runtime_repair",
)
