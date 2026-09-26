export type Num = number | null | undefined;

export interface SapB1ParametersState {
  loaded: boolean;
  valid?: boolean;
  error?: string | null;
  keys_total?: number;
  keys_set?: number;
  missing?: string[];
  using_default?: string[];
  branches?: number;
  accounts?: number;
}

export interface SapB1LastCycle {
  started_at?: string | null;
  finished_at?: string | null;
  status?: string | null;
  entities_ok?: number | null;
  entities_failed?: number | null;
}

export interface SapB1InitialLoad {
  state?: "none" | "running" | "done" | string | null;
  months_done?: number | null;
  months_total?: number | null;
}

export interface SapB1ConnectorState {
  present: boolean;
  readable?: boolean;
  error?: string | null;
  status?: string | null;
  age_seconds?: number | null;
  at?: string | null;
  agent_version?: string | null;
  source_ok?: boolean | null;
  source_ms?: number | null;
  source_error?: string | null;
  dialect?: string | null;
  companies?: string[] | null;
  last_cycle?: SapB1LastCycle | null;
  initial_load?: SapB1InitialLoad | null;
  next_cycle_at?: string | null;
}

export interface SapB1DagState {
  dag_id: string;
  present: boolean | null;
  paused: boolean | null;
}

export type SapB1Transport = "ses" | "smtp" | "sin_configurar";

export interface SapB1DigestState {
  recipients: number;
  transport: SapB1Transport | string;
  last: {
    local_date: string;
    status: string;
    delivered: number;
    recipients: number;
  } | null;
}

export interface SapB1Overview {
  installed: string | null;
  connection: { present: boolean };
  parameters: SapB1ParametersState;
  connector: SapB1ConnectorState;
  dags: SapB1DagState[];
  digest: SapB1DigestState;
}

export interface SapB1MappingEntity {
  entity: string;
  business_name?: string | null;
  description?: string | null;
  mode?: string | null;
  date_field?: string | null;
  primary_key?: string | null;
  fields: string[];
  datasets: string[];
}

export interface SapB1Mapping {
  entities: SapB1MappingEntity[];
}

export type SapB1LoadStatus = "ok" | "faltan" | "sobran" | "sin_conteo";

export interface SapB1LoadRow {
  company: string;
  entity: string;
  business_name?: string | null;
  mode?: string | null;
  dated?: boolean | null;
  window_start?: string | null;
  window_end?: string | null;
  counted_at?: string | null;
  source_rows?: Num;
  platform_rows?: Num;
  difference?: Num;
  loaded_pct?: Num;
  status?: SapB1LoadStatus | string | null;
}

export type SapB1Case = "finanzas" | "ventas" | "compras";

export interface SapB1Indicator {
  id: string;
  case: SapB1Case | string;
  name: string;
  formula: string;
  unit: string;
  dimensions: string[];
  granularity: string;
  dataset: string;
  filter: string;
  thresholds: string[];
}

export interface SapB1Indicators {
  indicators: SapB1Indicator[];
}

export interface SapB1Parameter {
  kind: "account" | "threshold" | "setting" | "branch" | string;
  company: string;
  period: string;
  key: string;
  value: string;
}

export interface SapB1CatalogEntry {
  key: string;
  kind: "threshold" | "setting" | string;
  unit: string;
  default: string | null;
  case: string;
  label: string;
}

export interface SapB1BusinessParameters {
  text: string;
  parameters: SapB1Parameter[];
  catalog: SapB1CatalogEntry[];
  keys_total: number;
  keys_set: number;
  missing: string[];
  using_default: string[];
  branches: number;
  accounts: number;
}

export interface SapB1SaveParametersResult {
  saved: boolean;
  count: number | null;
  refreshed: boolean;
  refresh_error: string | null;
}

export interface SapB1FinanceRunResult {
  rows: number | null;
  indicators: string[] | null;
  companies: Record<string, string[]> | null;
}

export interface SapB1Recipients {
  recipients: string[];
  max: number;
  transport: SapB1Transport | string;
}

export interface KpiMetric {
  status?: string | null;
  supported?: boolean | null;
  proxy_note?: string | null;
  error?: string | null;
  notes?: string[];
  period?: string | null;
  breaches?: string[];
}

export interface MarginTotalsRow {
  company?: string | null;
  value?: Num;
  pct?: Num;
  revenue?: Num;
  commission?: Num;
}

export interface MarginTotalsKpi extends KpiMetric {
  currency?: string | null;
  group?: MarginTotalsRow | null;
  companies?: MarginTotalsRow[];
  trailing_group_pct?: Num;
}

export interface DestroyersKpi extends KpiMetric {
  customers?: number | null;
  margin_lost?: Num;
  by_company?: Array<{ company?: string | null; customers?: number | null; margin_lost?: Num; min_margin_pct?: Num }>;
}

export interface ConcentrationKpi extends KpiMetric {
  by_company?: Array<{ company?: string | null; share_pct?: Num; top_margin?: Num; top_customers?: number | null }>;
}

export interface SellerMarginKpi extends KpiMetric {
  by_company?: Array<{ company?: string | null; sellers?: number | null; margin?: Num; best_pct?: Num; worst_pct?: Num }>;
}

export interface FinanceReconciliationKpi extends KpiMetric {
  rows?: number | null;
  within?: number | null;
  outside?: number | null;
  without_platform?: number | null;
  platform_only?: number | null;
  within_pct?: Num;
  periods?: string[];
}

export interface DataQualityKpi extends KpiMetric {
  checks?: number | null;
  checks_below_min?: number | null;
}

export interface EntityModelRow {
  entity?: string | null;
  company?: string | null;
  records?: number | null;
  identities?: number | null;
  shared_identities?: number | null;
  complete_records?: number | null;
  completeness_pct?: Num;
  orphans?: number | null;
  relation_rule?: string | null;
}

export interface EntityModelKpi extends KpiMetric {
  entities?: EntityModelRow[];
}

export interface DistributorMetricKpi extends KpiMetric {
  distributors?: Array<{ distributor?: string | null; value?: Num; color?: string | null }>;
}

export interface DistributorScorecardKpi extends KpiMetric {
  red?: number | null;
  yellow?: number | null;
  green?: number | null;
  without_thresholds?: number | null;
  distributors?: Array<{ distributor?: string | null; overall_color?: string | null }>;
}

export interface ClinicSellOutKpi extends KpiMetric {
  by_distributor?: Array<{
    distributor?: string | null;
    clinics?: number | null;
    revenue?: Num;
    units?: Num;
    top_share_pct?: Num;
    currency?: string | null;
  }>;
}

export interface BatchExpiryKpi extends KpiMetric {
  as_of?: string | null;
  levels?: Record<string, { batches?: number | null; value?: Num; at_risk_value?: Num }>;
  at_risk_value?: Num;
  options?: Record<string, number>;
}

export interface CoverageKpi extends KpiMetric {
  as_of?: string | null;
  colors?: Record<string, number>;
  stockout_risk?: number | null;
  critical_at_risk?: number | null;
  plan_changes?: number | null;
  suggestions?: number | null;
}

export interface PurchaseNeedKpi extends KpiMetric {
  items_with_need?: number | null;
  items_short?: number | null;
}

export interface CostVarianceKpi extends KpiMetric {
  items?: number | null;
  above_threshold?: number | null;
  variance_value?: Num;
}

export interface SupplierLeadTimeKpi extends KpiMetric {
  suppliers?: number | null;
  late_suppliers?: Array<{ company?: string | null; supplier?: string | null; on_time_pct?: Num }>;
}

export interface LearningSourceRow {
  source?: string | null;
  alerts?: number | null;
  decisions?: number | null;
  outcomes?: number | null;
  false_positives?: number | null;
  achieved?: number | null;
  not_achieved?: number | null;
}

export interface LearningKpi extends KpiMetric {
  window_days?: number | null;
  sources?: LearningSourceRow[];
  suggestions?: Array<{ source?: string | null; thresholds?: string[]; reason?: string | null }>;
}

export interface SapB1ViewBase<M> {
  domain?: string | null;
  generated_at?: string | null;
  status?: string | null;
  notes?: string[];
  unavailable_metrics?: string[];
  degraded_metrics?: string[];
  metrics?: Partial<M>;
}

export interface SapB1MarginMetrics {
  margen_bruto: MarginTotalsKpi;
  margen_contribucion: MarginTotalsKpi;
  destructores: DestroyersKpi;
  concentracion_top20: ConcentrationKpi;
  margen_vendedor: SellerMarginKpi;
  reconciliacion_finanzas: FinanceReconciliationKpi;
  calidad_datos: DataQualityKpi;
  modelo_entidades: EntityModelKpi;
}

export interface SapB1SalesMetrics {
  ratio_sellout_sellin: DistributorMetricKpi;
  dias_inventario: DistributorMetricKpi;
  sellout_clinica: ClinicSellOutKpi;
  semaforo_distribuidoras: DistributorScorecardKpi;
}

export interface SapB1ExpiryMetrics {
  caducidad_lotes: BatchExpiryKpi;
}

export interface SapB1SupplyMetrics {
  dias_cobertura: CoverageKpi;
  oc_vs_necesidad: PurchaseNeedKpi;
  costo_real_vs_estandar: CostVarianceKpi;
  lead_time_proveedores: SupplierLeadTimeKpi;
}

export interface SapB1LearningMetrics {
  aprendizaje: LearningKpi;
}

export interface SapB1SemaforoMetrics {
  margen_bruto: MarginTotalsKpi;
  destructores: DestroyersKpi;
  reconciliacion_finanzas: FinanceReconciliationKpi;
  semaforo_distribuidoras: DistributorScorecardKpi;
  caducidad_lotes: BatchExpiryKpi;
  dias_cobertura: CoverageKpi;
  calidad_datos: DataQualityKpi;
}

export interface SapB1ViewMetrics {
  sap_b1_margin_kpis: SapB1MarginMetrics;
  sap_b1_sales_kpis: SapB1SalesMetrics;
  sap_b1_expiry_kpis: SapB1ExpiryMetrics;
  sap_b1_supply_kpis: SapB1SupplyMetrics;
  sap_b1_learning_kpis: SapB1LearningMetrics;
  sap_b1_semaforo_kpis: SapB1SemaforoMetrics;
}

export type SapB1ViewName = keyof SapB1ViewMetrics;

export type SapB1View<V extends SapB1ViewName> = SapB1ViewBase<SapB1ViewMetrics[V]>;

export type SapB1AnyMetric = KpiMetric & Record<string, unknown>;
