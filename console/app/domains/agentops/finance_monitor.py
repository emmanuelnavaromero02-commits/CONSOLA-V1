"""Finance AgentOps monitor contract (Controller Financiero).

Watches the three Mission 1 Finance aggregates through the Mission 2 view
``control_room__finance_kpis_read``: billable hours logged, labor cost by
department, and project margin.

The conversational Controller Financiero stays exactly as it is: a global
template row (``workspace_id IS NULL``) that answers questions. A scheduled
monitor cannot be that row, because ``agent_runner`` has to mint a signed
tenant/workspace security_context before it can call MCP, and
``sync_agentops_monitor_candidates`` skips any agent without both ids. So the
monitor is its own workspace-scoped row, which is the same separation
``99t_sap_successfactors_talent_agentops_monitor.sql`` documents for Talent
against the global Talent Advisor. It reuses the Controller's personality
verbatim so the voice does not change between the two surfaces.
"""

from __future__ import annotations

from typing import Any

from app.domains.agentops.domain_monitor_support import (
    DomainMonitorSpec,
    build_contract,
    is_domain_monitor_row,
    needs_runtime_repair,
)

FINANCE_MONITOR_CARTRIDGE = "sap_s4hana"
FINANCE_MONITOR_SLUG = "sap_s4hana_controller_financiero_monitor"
FINANCE_WISDOM_BIT_ID = "WB-FINANZAS"

# Subjects this monitor reads from and writes to shared memory. cost_center_budget
# is the real gap from Mission 1: there is no budget source in any cartridge, so
# budget_vs_actual_by_cost_center does not exist as a function, and Risk hits the
# same wall with cost_center_overrun.
FINANCE_MEMORY_SUBJECTS = (
    "cost_center_budget",
    "pnl_mensual.base_currency",
)

FINANCE_INSTRUCTIONS = """Eres el monitor programado de Finanzas para Control Room.

## Objetivo
Revisar, de forma segura y auditable, el estado agregado de Finanzas: horas facturables registradas, costo de nomina por departamento y margen por proyecto. No escribes en SAP, no apruebas nada y no expones datos de personas.

## Que vigilas
1. Margen por proyecto: proyectos con margen negativo o en deterioro sostenido.
2. Costo de nomina por departamento contra las horas ejecutadas del mes cerrado.
3. Horas facturables registradas: caidas respecto al periodo anterior.

## Cuando alertas
Alerta cuando el estado del dominio no es `ready` y hay al menos una senal concreta. Si el dominio esta `unavailable` NO alertas: no hay evidencia suficiente y una alerta sin datos es ruido.

## Que evidencia citas
Solo agregados: totales, conteos, porcentajes y el periodo. Nunca un nombre de persona, un userid ni un identificador de empleado. Di siempre sobre que ventana temporal hablas.

## Honestidad obligatoria
- El margen se calcula sobre importes base porque `pnl_mensual` no tiene moneda verificada; dilo cuando reportes margen.
- Las horas facturables son un proxy de horas validadas sin facturar, no el dato facturado real.
- Antes de reportar una limitacion de datos, consulta la memoria compartida: si otro agente ya registro ese hallazgo, citalo en lugar de reportarlo como nuevo.

## Regla de seguridad
Todas las salidas son recommendation_only. No hay write-back externo ni acciones destructivas."""

FINANCE_MONITOR_SPEC = DomainMonitorSpec(
    key="finance",
    cartridge_id=FINANCE_MONITOR_CARTRIDGE,
    slug=FINANCE_MONITOR_SLUG,
    name="Controller Financiero Monitor",
    domain="Finanzas",
    wisdom_bit_id=FINANCE_WISDOM_BIT_ID,
    kpi_tool="control_room__finance_kpis_read",
    source_label="finance_kpis",
    description=(
        "Monitor programado de Finanzas: revisa margen por proyecto, costo de "
        "nomina por departamento y horas facturables, y publica evidencia "
        "advisory en Control Room."
    ),
    instructions=FINANCE_INSTRUCTIONS,
    # Reused verbatim from the conversational Controller Financiero seed
    # (infra/init/86_sap_s4hana_agents_seed.sql) so the voice is the same.
    personality=(
        "Riguroso y analitico, tono Controller. Cifras y riesgo arriba, "
        "evidencia abajo. Honesto sobre datos parciales. Idioma del usuario."
    ),
    recommended_action=(
        "Revisar proyectos con margen negativo y el costo de nomina del mes "
        "cerrado antes de comprometer nueva capacidad."
    ),
    # Staggered against the other two domains so three monitors never contend
    # for the same minute.
    cron="2,17,32,47 * * * *",
    calibration_group="sap_s4hana:finance_margin",
    monte_carlo_seed=45210,
    decision_title="Decision operativa WB-FINANZAS",
    decision_description=(
        "Evaluar si las senales agregadas de margen y costo requieren abrir "
        "investigacion con seguimiento supervisado."
    ),
    risk_metric="project_margin_delta",
    rag_cartridges=("sap_s4hana",),
    # Margin and cost move with FX and rates, so Banxico/INEGI context applies.
    uses_market_context=True,
    memory_subjects=FINANCE_MEMORY_SUBJECTS,
)


def finance_monitor_contract() -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    """``(allowed_tools, rag_filter, extra)`` for the Finance monitor."""
    return build_contract(FINANCE_MONITOR_SPEC)


def is_finance_monitor_row(agent: Any) -> bool:
    return is_domain_monitor_row(agent, FINANCE_MONITOR_SPEC)


def finance_monitor_needs_runtime_repair(agent: Any) -> bool:
    return needs_runtime_repair(agent, FINANCE_MONITOR_SPEC)


__all__ = (
    "FINANCE_MEMORY_SUBJECTS",
    "FINANCE_MONITOR_CARTRIDGE",
    "FINANCE_MONITOR_SLUG",
    "FINANCE_MONITOR_SPEC",
    "FINANCE_WISDOM_BIT_ID",
    "finance_monitor_contract",
    "finance_monitor_needs_runtime_repair",
    "is_finance_monitor_row",
)
