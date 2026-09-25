"""SAP Business One AgentOps monitors (one per business case)."""

from __future__ import annotations

from app.domains.agentops.domain_monitor_support import DomainMonitorSpec

SAP_B1_CARTRIDGE = "sap_b1"
SAP_B1_TZ = "America/Mexico_City"

SAP_B1_MARGIN_INSTRUCTIONS = """Eres el monitor programado de margen de SAP Business One para Control Room.

## Objetivo
Revisar cada manana el margen del grupo y de cada empresa del ultimo mes cerrado, los clientes y familias bajo el margen minimo, la venta bajo costo, la reconciliacion contra los totales de finanzas y la calidad de datos. No escribes en SAP y no apruebas nada.

## Cuando alertas
Alertas cuando la vista trae incumplimientos (breaches) o cuando una metrica no esta disponible por falta de datos. Si el dominio esta unavailable NO alertas.

## Que evidencia citas
Solo agregados, el periodo y los incumplimientos tal como vienen. Los nombres de cliente solo aparecen si se pidieron explicitamente.

## Honestidad obligatoria
- El margen del grupo elimina la utilidad intercompania que sigue en inventario; no conviertas monedas.
- Si finanzas no entrego totales de control, dilo: la reconciliacion queda sin control, no aprobada.
- Los motores de simulacion, calibracion y decision estan deshabilitados; no afirmes que corriste escenarios.

## Regla de seguridad
Todas las salidas son recommendation_only. No hay write-back externo."""

SAP_B1_MARGIN_MONITOR_SPEC = DomainMonitorSpec(
    key="sap_b1_margin",
    cartridge_id=SAP_B1_CARTRIDGE,
    slug="sap_b1_margin_monitor",
    name="Monitor de Margen SAP Business One",
    domain="Finanzas",
    wisdom_bit_id="WB-B1-MARGEN",
    kpi_tool="control_room__sap_b1_kpis_read",
    source_label="sap_b1_margin_kpis",
    description=(
        "Monitor diario de margen de SAP Business One: grupo, empresas, clientes, "
        "familias, venta bajo costo, reconciliacion con finanzas y calidad de datos."
    ),
    instructions=SAP_B1_MARGIN_INSTRUCTIONS,
    personality="Controller riguroso: cifras y periodo arriba, evidencia abajo, honesto con los huecos.",
    recommended_action=(
        "Revisar las empresas, clientes y familias bajo el margen minimo y las "
        "diferencias de reconciliacion antes del comite."
    ),
    cron="20 7 * * *",
    tz=SAP_B1_TZ,
    calibration_group="sap_b1:margin",
    monte_carlo_seed=51120,
    decision_title="Decision operativa WB-B1-MARGEN",
    decision_description="Evaluar si los incumplimientos de margen requieren abrir seguimiento supervisado.",
    risk_metric="group_margin_delta",
    rag_cartridges=(SAP_B1_CARTRIDGE,),
    uses_market_context=True,
    memory_subjects=("margen_minimo", "totales_de_control_finanzas"),
)

SAP_B1_MONITOR_SPECS: tuple[DomainMonitorSpec, ...] = (SAP_B1_MARGIN_MONITOR_SPEC,)

__all__ = ("SAP_B1_CARTRIDGE", "SAP_B1_MARGIN_MONITOR_SPEC", "SAP_B1_MONITOR_SPECS", "SAP_B1_TZ")
