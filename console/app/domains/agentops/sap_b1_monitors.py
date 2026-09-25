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

SAP_B1_EXPIRY_INSTRUCTIONS = """Eres el monitor programado de caducidad de SAP Business One para Control Room.

## Objetivo
Revisar cada manana los lotes con existencia: lo ya vencido, lo que vence dentro del horizonte y las unidades que se venceran sin venderse al ritmo actual, con la accion sugerida por articulo (traspaso a otra empresa del grupo que vende mas rapido, promocion o devolucion). No escribes en SAP.

## Cuando alertas
Alertas cuando hay lotes vencidos con existencia o articulos en riesgo dentro del horizonte. Si el dominio esta unavailable NO alertas.

## Honestidad obligatoria
- El riesgo supone que el ritmo de venta de los ultimos 90 dias se mantiene y que se vende primero lo que vence primero.
- No consideres promociones ni traspasos que no estan en los datos.

## Regla de seguridad
Todas las salidas son recommendation_only. No hay write-back externo."""

SAP_B1_EXPIRY_MONITOR_SPEC = DomainMonitorSpec(
    key="sap_b1_expiry",
    cartridge_id=SAP_B1_CARTRIDGE,
    slug="sap_b1_expiry_monitor",
    name="Monitor de Caducidad SAP Business One",
    domain="Operacion",
    wisdom_bit_id="WB-B1-CADUCIDAD",
    kpi_tool="control_room__sap_b1_kpis_read",
    source_label="sap_b1_expiry_kpis",
    description="Monitor diario de caducidad: lotes vencidos, en riesgo y accion sugerida por articulo.",
    instructions=SAP_B1_EXPIRY_INSTRUCTIONS,
    personality="Jefe de almacen practico: que vence, cuanto vale y que hacer hoy.",
    recommended_action="Mover o promover los articulos en riesgo antes de que venzan y dar de baja lo ya vencido.",
    cron="25 7 * * *",
    tz=SAP_B1_TZ,
    calibration_group="sap_b1:expiry",
    monte_carlo_seed=51125,
    decision_title="Decision operativa WB-B1-CADUCIDAD",
    decision_description="Evaluar traspasos o promociones para lotes en riesgo de caducar.",
    risk_metric="expiry_at_risk_value",
    rag_cartridges=(SAP_B1_CARTRIDGE,),
    memory_subjects=("caducidad_lotes",),
)

SAP_B1_MONITOR_SPECS: tuple[DomainMonitorSpec, ...] = (SAP_B1_MARGIN_MONITOR_SPEC, SAP_B1_EXPIRY_MONITOR_SPEC)

__all__ = (
    "SAP_B1_CARTRIDGE",
    "SAP_B1_EXPIRY_MONITOR_SPEC",
    "SAP_B1_MARGIN_MONITOR_SPEC",
    "SAP_B1_MONITOR_SPECS",
    "SAP_B1_TZ",
)
