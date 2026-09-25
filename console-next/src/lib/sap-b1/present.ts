import type {
  BatchExpiryKpi,
  ClinicSellOutKpi,
  ConcentrationKpi,
  CostVarianceKpi,
  CoverageKpi,
  DataQualityKpi,
  DestroyersKpi,
  DistributorMetricKpi,
  DistributorScorecardKpi,
  FinanceReconciliationKpi,
  KpiMetric,
  MarginTotalsKpi,
  Num,
  PurchaseNeedKpi,
  SapB1Case,
  SapB1Overview,
  SapB1ViewName,
  SellerMarginKpi,
  SupplierLeadTimeKpi,
} from "./types";

export const HEARTBEAT_LIMIT_SECONDS = 15 * 60;

export const METRIC_LABELS: Record<string, string> = {
  margen_bruto: "Margen bruto",
  margen_contribucion: "Margen de contribución",
  destructores: "Destructores de margen",
  concentracion_top20: "Concentración del margen en el 20 % de clientes",
  margen_vendedor: "Margen por vendedor",
  reconciliacion_finanzas: "Reconciliación con Finanzas",
  calidad_datos: "Calidad de datos",
  modelo_entidades: "Modelo de entidades",
  ratio_sellout_sellin: "Ratio sell-out / sell-in",
  dias_inventario: "Días de inventario en canal",
  sellout_clinica: "Sell-out por clínica",
  semaforo_distribuidoras: "Semáforo de distribuidoras",
  caducidad_lotes: "Caducidad de lotes",
  dias_cobertura: "Días de cobertura",
  oc_vs_necesidad: "Órdenes de compra contra necesidad",
  costo_real_vs_estandar: "Costo real contra estándar",
  lead_time_proveedores: "Entregas de proveedores",
  aprendizaje: "Aprendizaje de decisiones",
};

export const METRIC_VIEW: Record<string, SapB1ViewName> = {
  margen_bruto: "sap_b1_margin_kpis",
  margen_contribucion: "sap_b1_margin_kpis",
  destructores: "sap_b1_margin_kpis",
  concentracion_top20: "sap_b1_margin_kpis",
  margen_vendedor: "sap_b1_margin_kpis",
  reconciliacion_finanzas: "sap_b1_margin_kpis",
  calidad_datos: "sap_b1_margin_kpis",
  modelo_entidades: "sap_b1_margin_kpis",
  ratio_sellout_sellin: "sap_b1_sales_kpis",
  dias_inventario: "sap_b1_sales_kpis",
  sellout_clinica: "sap_b1_sales_kpis",
  semaforo_distribuidoras: "sap_b1_sales_kpis",
  caducidad_lotes: "sap_b1_expiry_kpis",
  dias_cobertura: "sap_b1_supply_kpis",
  oc_vs_necesidad: "sap_b1_supply_kpis",
  costo_real_vs_estandar: "sap_b1_supply_kpis",
  lead_time_proveedores: "sap_b1_supply_kpis",
  aprendizaje: "sap_b1_learning_kpis",
};

export const CASES: Array<{ id: SapB1Case; label: string; app: string; appLabel: string; extras: string[] }> = [
  { id: "finanzas", label: "Finanzas", app: "sap_b1_margen", appLabel: "Margen", extras: ["reconciliacion_finanzas", "calidad_datos"] },
  { id: "ventas", label: "Ventas", app: "sap_b1_sellout", appLabel: "Sell-out", extras: ["semaforo_distribuidoras"] },
  { id: "compras", label: "Compras", app: "sap_b1_abasto", appLabel: "Abasto", extras: ["lead_time_proveedores"] },
];

export function analyticAppHref(app: string): string {
  return `/analytics/viewer?app=${encodeURIComponent(app)}`;
}

export function metricLabel(metric: string): string {
  return METRIC_LABELS[metric] ?? metric;
}

const NUMBER = new Intl.NumberFormat("es-MX", { maximumFractionDigits: 0 });
const DECIMAL = new Intl.NumberFormat("es-MX", { maximumFractionDigits: 1 });

function finite(value: Num): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function formatCount(value: Num): string {
  return finite(value) ? NUMBER.format(value) : "N/D";
}

export function countLabel(value: Num, singular: string, plural: string): string {
  return `${formatCount(value)} ${value === 1 ? singular : plural}`;
}

export function formatDecimal(value: Num): string {
  return finite(value) ? DECIMAL.format(value) : "N/D";
}

export function formatPct(value: Num): string {
  return finite(value) ? `${DECIMAL.format(value)} %` : "N/D";
}

export function formatMoney(value: Num, currency?: string | null): string {
  if (!finite(value)) return "N/D";
  return currency ? `${NUMBER.format(value)} ${currency}` : NUMBER.format(value);
}

export function formatDateTime(value?: string | null): string {
  if (!value) return "N/D";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" });
}

export function formatAge(seconds: Num): string {
  if (!finite(seconds)) return "N/D";
  if (seconds < 60) return `hace ${Math.round(seconds)} s`;
  if (seconds < 3600) return `hace ${Math.round(seconds / 60)} min`;
  if (seconds < 86_400) return `hace ${DECIMAL.format(seconds / 3600)} h`;
  return `hace ${DECIMAL.format(seconds / 86_400)} días`;
}

export type MetricState = "ready" | "degraded" | "unavailable" | "missing";

export function metricState(metric: KpiMetric | null | undefined): MetricState {
  const status = String(metric?.status ?? "").toLowerCase();
  if (!metric) return "missing";
  if (status === "ready") return "ready";
  if (status === "degraded") return "degraded";
  return "unavailable";
}

export const METRIC_STATE_LABELS: Record<MetricState, string> = {
  ready: "Listo",
  degraded: "Parcial",
  unavailable: "Sin datos",
  missing: "Sin datos todavía",
};

export function metricReason(metric: KpiMetric | null | undefined): string | null {
  if (!metric) return null;
  if (metric.error?.trim()) return metric.error.trim();
  return metric.notes?.find((note) => note.trim())?.trim() ?? null;
}

export type AreaColor = "rojo" | "amarillo" | "verde" | "sin_datos";

export const AREA_COLOR_LABELS: Record<AreaColor, string> = {
  rojo: "Rojo",
  amarillo: "Amarillo",
  verde: "Verde",
  sin_datos: "Sin datos",
};

const AREA_ORDER: Record<AreaColor, number> = { rojo: 0, amarillo: 1, sin_datos: 2, verde: 3 };
const MAX_AREA_FINDINGS = 5;

export function areaColor(metric: KpiMetric | null | undefined): AreaColor {
  const status = String(metric?.status ?? "").trim().toLowerCase();
  if (!metric || status === "unavailable") return "sin_datos";
  if ((metric.breaches ?? []).some((breach) => typeof breach === "string" && breach.trim())) return "rojo";
  return status === "degraded" ? "amarillo" : "verde";
}

export interface SemaforoArea {
  metric: string;
  label: string;
  color: AreaColor;
  period: string | null;
  findings: string[];
  findingsTotal: number;
  reason: string | null;
}

export function semaforoAreas(metrics: Record<string, KpiMetric | undefined> | null | undefined): SemaforoArea[] {
  const areas: SemaforoArea[] = [];
  for (const [metric, item] of Object.entries(metrics ?? {})) {
    if (!item || typeof item !== "object") continue;
    const findings = (item.breaches ?? []).filter((breach) => typeof breach === "string" && breach.trim()).map((breach) => breach.trim());
    const asOf = (item as { as_of?: string | null }).as_of;
    areas.push({
      metric,
      label: metricLabel(metric),
      color: areaColor(item),
      period: item.period || asOf || null,
      findings: findings.slice(0, MAX_AREA_FINDINGS),
      findingsTotal: findings.length,
      reason: findings.length ? null : metricReason(item),
    });
  }
  return areas.sort((a, b) => AREA_ORDER[a.color] - AREA_ORDER[b.color] || a.label.localeCompare(b.label, "es"));
}

export function semaforoHeadline(areas: SemaforoArea[]): string {
  const red = areas.filter((area) => area.color === "rojo").length;
  const missing = areas.filter((area) => area.color === "sin_datos").length;
  if (red) return `${red} área${red === 1 ? "" : "s"} en rojo`;
  if (missing) return `sin rojos, ${missing} área${missing === 1 ? "" : "s"} sin datos`;
  return areas.length ? "todo en verde" : "sin áreas todavía";
}

export interface MetricSummary {
  value: string;
  detail: string | null;
}

function joinParts(parts: Array<string | null | undefined | false>): string | null {
  const kept = parts.filter((part): part is string => Boolean(part));
  return kept.length ? kept.join(" · ") : null;
}

function countColors(rows: Array<{ color?: string | null }> | undefined): string | null {
  const counts: Record<string, number> = {};
  for (const row of rows ?? []) {
    const color = row.color || "sin_umbral";
    counts[color] = (counts[color] ?? 0) + 1;
  }
  return joinParts(
    ["rojo", "amarillo", "verde", "sin_umbral"].map((color) =>
      counts[color] ? `${counts[color]} ${color === "sin_umbral" ? "sin umbral" : color}` : null,
    ),
  );
}

function marginSummary(metric: MarginTotalsKpi): MetricSummary {
  const group = metric.group;
  const companies = (metric.companies ?? []).map((row) => `${row.company ?? "?"} ${formatPct(row.pct)}`);
  if (group && (finite(group.value) || finite(group.pct))) {
    return { value: `${formatPct(group.pct)} grupo`, detail: joinParts([formatMoney(group.value, metric.currency), ...companies]) };
  }
  if (companies.length) return { value: companies[0], detail: joinParts(companies.slice(1)) };
  return { value: "Sin datos", detail: null };
}

export function metricSummary(metricId: string, metric: KpiMetric | null | undefined): MetricSummary {
  const state = metricState(metric);
  if (!metric || state === "missing") return { value: "Sin datos todavía", detail: null };
  if (state === "unavailable") return { value: "Sin datos", detail: metricReason(metric) };
  switch (metricId) {
    case "margen_bruto":
    case "margen_contribucion":
      return marginSummary(metric as MarginTotalsKpi);
    case "destructores": {
      const item = metric as DestroyersKpi;
      return { value: countLabel(item.customers, "cliente", "clientes"), detail: `margen perdido ${formatMoney(item.margin_lost)}` };
    }
    case "concentracion_top20": {
      const rows = (metric as ConcentrationKpi).by_company ?? [];
      return { value: rows.length === 1 ? formatPct(rows[0].share_pct) : `${rows.length} empresas`, detail: joinParts(rows.map((row) => `${row.company ?? "?"} ${formatPct(row.share_pct)}`)) };
    }
    case "margen_vendedor": {
      const rows = (metric as SellerMarginKpi).by_company ?? [];
      const sellers = rows.reduce((total, row) => total + (row.sellers ?? 0), 0);
      return { value: countLabel(sellers, "vendedor", "vendedores"), detail: joinParts(rows.map((row) => `${row.company ?? "?"}: mejor ${formatPct(row.best_pct)}, peor ${formatPct(row.worst_pct)}`)) };
    }
    case "reconciliacion_finanzas": {
      const item = metric as FinanceReconciliationKpi;
      if (!item.rows) return { value: "Sin corrida de Finanzas", detail: metricReason(item) };
      return {
        value: `${formatPct(item.within_pct)} cuadra`,
        detail: joinParts([`${formatCount(item.within)} de ${formatCount(item.rows)} filas dentro`, `${formatCount(item.outside)} fuera`, item.without_platform ? `${formatCount(item.without_platform)} sin plataforma` : null]),
      };
    }
    case "calidad_datos": {
      const item = metric as DataQualityKpi;
      return { value: `${formatCount(item.checks_below_min)} bajo mínimo`, detail: countLabel(item.checks, "control", "controles") };
    }
    case "ratio_sellout_sellin":
    case "dias_inventario": {
      const rows = (metric as DistributorMetricKpi).distributors ?? [];
      return { value: countLabel(rows.length, "distribuidora", "distribuidoras"), detail: countColors(rows) };
    }
    case "semaforo_distribuidoras": {
      const item = metric as DistributorScorecardKpi;
      return {
        value: `${formatCount(item.red)} en rojo`,
        detail: joinParts([`${formatCount(item.yellow)} amarillo`, `${formatCount(item.green)} verde`, item.without_thresholds ? `${formatCount(item.without_thresholds)} sin umbral` : null]),
      };
    }
    case "sellout_clinica": {
      const rows = (metric as ClinicSellOutKpi).by_distributor ?? [];
      const clinics = rows.reduce((total, row) => total + (row.clinics ?? 0), 0);
      return { value: countLabel(clinics, "clínica", "clínicas"), detail: joinParts(rows.map((row) => `${row.distributor ?? "?"} ${formatMoney(row.revenue, row.currency)}`)) };
    }
    case "caducidad_lotes": {
      const item = metric as BatchExpiryKpi;
      const levels = item.levels ?? {};
      return {
        value: `${formatMoney(item.at_risk_value)} en riesgo`,
        detail: joinParts(["vencido", "rojo", "amarillo", "verde"].map((level) => (levels[level] ? `${level} ${countLabel(levels[level].batches, "lote", "lotes")}` : null))),
      };
    }
    case "dias_cobertura": {
      const item = metric as CoverageKpi;
      return {
        value: `${formatCount(item.colors?.rojo ?? 0)} en rojo`,
        detail: joinParts([`${formatCount(item.stockout_risk)} con riesgo de quiebre`, `${formatCount(item.critical_at_risk)} críticos`, item.plan_changes ? `${formatCount(item.plan_changes)} cambios de plan` : null]),
      };
    }
    case "oc_vs_necesidad": {
      const item = metric as PurchaseNeedKpi;
      return { value: countLabel(item.items_short, "corto", "cortos"), detail: `${countLabel(item.items_with_need, "artículo", "artículos")} con necesidad` };
    }
    case "costo_real_vs_estandar": {
      const item = metric as CostVarianceKpi;
      return { value: `${formatCount(item.above_threshold)} de ${formatCount(item.items)} fuera`, detail: `desviación ${formatMoney(item.variance_value)}` };
    }
    case "lead_time_proveedores": {
      const item = metric as SupplierLeadTimeKpi;
      return { value: countLabel(item.late_suppliers?.length ?? 0, "tardío", "tardíos"), detail: countLabel(item.suppliers, "proveedor", "proveedores") };
    }
    default:
      return { value: METRIC_STATE_LABELS[state], detail: metricReason(metric) };
  }
}

export type CheckState = "ok" | "pendiente" | "desconocido";

export interface ChecklistItem {
  id: string;
  label: string;
  state: CheckState;
  detail: string;
}

function dagLabel(dagId: string): string {
  if (dagId === "sap_b1_refresh") return "Extracción SAP B1";
  if (dagId === "dataset_refresh_chain") return "Cadena de datasets";
  if (dagId === "agent_runner") return "Agentes programados";
  return dagId;
}

export const TRANSPORT_LABELS: Record<string, string> = {
  ses: "Amazon SES",
  smtp: "SMTP",
  sin_configurar: "Transporte de correo sin configurar",
};

const DELIVERY_LABELS: Record<string, string> = { sent: "enviado", failed: "falló", sending: "enviando" };

export function deliveryLabel(status?: string | null): string {
  return status ? DELIVERY_LABELS[status] ?? status : "N/D";
}

export function setupChecklist(overview: SapB1Overview): ChecklistItem[] {
  const items: ChecklistItem[] = [];
  const installed = overview.installed;
  items.push({
    id: "installed",
    label: "Cartucho instalado",
    state: installed === "active" || installed === "ready" ? "ok" : "pendiente",
    detail: installed ? `Estado de la instalación: ${installed}` : "Sin instalación en este workspace",
  });
  items.push({
    id: "vault",
    label: "Conexión en Vault",
    state: overview.connection?.present ? "ok" : "pendiente",
    detail: overview.connection?.present ? "Conexión sap_b1/default registrada" : "Falta registrar la conexión sap_b1/default",
  });
  const parameters = overview.parameters ?? { loaded: false };
  const missing = parameters.missing ?? [];
  items.push({
    id: "parameters",
    label: "Parámetros completos",
    state: parameters.valid === false ? "pendiente" : parameters.loaded && missing.length === 0 ? "ok" : "pendiente",
    detail: parameters.valid === false
      ? `Parámetros inválidos: ${parameters.error ?? "revisa el texto"}`
      : !parameters.loaded
        ? "Sin parámetros cargados"
        : missing.length
          ? `Faltan ${missing.length}: ${missing.join(", ")}`
          : `${formatCount(parameters.keys_set)} de ${formatCount(parameters.keys_total)} definidos · ${countLabel(parameters.branches, "filial", "filiales")} · ${countLabel(parameters.accounts, "lista de cuentas", "listas de cuentas")}`,
  });
  const connector = overview.connector ?? { present: false };
  const age = connector.age_seconds;
  const online = Boolean(connector.present && finite(age) && age < HEARTBEAT_LIMIT_SECONDS);
  items.push({
    id: "connector",
    label: "Conector en línea",
    state: online ? "ok" : "pendiente",
    detail: !connector.present
      ? "Sin heartbeat del conector"
      : !finite(age)
        ? "Heartbeat ilegible"
        : `Último heartbeat ${formatAge(age)}${online ? "" : " (más de 15 min)"}${connector.source_ok === false ? " · sin acceso a Business One" : ""}`,
  });
  const initial = connector.initial_load;
  items.push({
    id: "initial_load",
    label: "Carga inicial",
    state: initial?.state === "done" ? "ok" : initial ? "pendiente" : "desconocido",
    detail: !initial
      ? "Sin dato del conector"
      : initial.state === "done"
        ? `Completa: ${formatCount(initial.months_total)} meses`
        : initial.state === "running"
          ? `En curso: ${formatCount(initial.months_done)} de ${formatCount(initial.months_total)} meses`
          : "Sin iniciar",
  });
  for (const dag of overview.dags ?? []) {
    items.push({
      id: `dag:${dag.dag_id}`,
      label: `DAG ${dag.dag_id}`,
      state: dag.present === null ? "desconocido" : dag.present && dag.paused === false ? "ok" : "pendiente",
      detail: dag.present === null
        ? `${dagLabel(dag.dag_id)}: no se pudo consultar Airflow`
        : !dag.present
          ? `${dagLabel(dag.dag_id)}: no existe en Airflow`
          : `${dagLabel(dag.dag_id)}: ${dag.paused ? "pausado" : "activo"}`,
    });
  }
  const digest = overview.digest;
  const transportReady = digest?.transport === "ses" || digest?.transport === "smtp";
  items.push({
    id: "digest",
    label: "Destinatarios y transporte de correo",
    state: transportReady && (digest?.recipients ?? 0) > 0 ? "ok" : "pendiente",
    detail: `${countLabel(digest?.recipients ?? 0, "destinatario", "destinatarios")} · ${TRANSPORT_LABELS[digest?.transport ?? ""] ?? digest?.transport ?? "N/D"}`,
  });
  return items;
}
