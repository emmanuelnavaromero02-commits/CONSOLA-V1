export type Severity = "critical" | "high" | "medium" | "low";
export type SourceState = "ok" | "empty" | "missing" | "unavailable" | "invalid_schema" | "blocked" | "no_permission";
export type DataReadiness =
  | "ready"
  | "partial"
  | "stub"
  | "empty"
  | "missing"
  | "unavailable"
  | "invalid_schema"
  | "blocked"
  | "no_permission"
  | "error"
  | "benchmark_internal"
  | "insufficient_data"
  | "blocked_by_sap"
  | "blocked_by_permission"
  | "pending_approval"
  | "partial_fields";
export type SourceRollup = SourceState | "partial" | "stub" | "attention" | "inactive" | "no_sources" | "error";
export type LoadState = "loading" | "ready" | "error";
export type DetailMode = "auto" | "manual" | null;
export type AlertOperation = "ack" | "snooze" | "assign" | "false-positive";

export interface SourceStatus {
  cartridge?: string | null;
  domain?: string | null;
  module?: string | null;
  status?: SourceState | null;
  count: number;
  data_readiness?: DataReadiness | null;
  operationally_ready?: boolean | null;
  checked_at?: string | null;
}

export interface Kpi {
  label: string;
  value: string | number;
  tone: "neutral" | "attention";
  bad?: boolean;
}

export interface DomainModule {
  id?: string | null;
  label?: string | null;
  domain?: string | null;
  accent?: string | null;
  description?: string | null;
  status?: string | null;
  active?: boolean | null;
  operational?: boolean | null;
  item_count: number;
  critical_count: number;
  cartridge_count?: number;
  source_status?: SourceRollup | null;
  data_readiness?: DataReadiness | null;
  operationally_ready?: boolean | null;
  modules?: DomainModule[];
}

export type Domain = DomainModule;

export type Cartridge = DomainModule;

export interface OmegaOption {
  id?: string | null;
  label?: string | null;
  score?: number | null;
  risk?: string | null;
  recommendation?: string | null;
  selected?: boolean | null;
}

export interface WritebackCapability {
  supported: boolean;
  status: string;
  mode?: string;
  external?: boolean;
  reason?: string;
  description?: string;
}

export interface ActionTemplate {
  template_id: string;
  label: string;
  description: string;
  risk_level: string;
  mode_default: string;
  requires_approval: boolean;
  writeback?: WritebackCapability;
}

export interface ImpactDriver {
  label?: string | null;
  value?: string | number | boolean | null;
  currency?: string | null;
  unit?: string | null;
  points?: number | null;
}

export interface ImpactPayload {
  item_id?: string;
  status?: string | null;
  estimate?: number | null;
  currency?: string | null;
  confidence?: number | null;
  priority_score?: number | null;
  explanation?: string | null;
  drivers?: ImpactDriver[];
}

export interface DetectionThreshold {
  anomaly_type?: string | null;
  metric?: string | null;
  warning_value?: number | null;
  critical_value?: number | null;
  currency?: string | null;
  enabled?: boolean | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ThresholdPayload {
  thresholds: DetectionThreshold[];
  summary?: {
    total: number;
    active: number;
    disabled: number;
    recent?: DetectionThreshold[];
  };
}

export interface ControlRoomAgentsOpsRun {
  agent_name?: string | null;
  status?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  tool_count: number;
}

export interface ControlRoomAgentsOpsEngine {
  engine?: string | null;
  configured: number;
  evidence_count: number;
  sample_count?: number;
  latest_at?: string | null;
  status?: "ready" | "configured" | "missing" | string | null;
  executions?: {
    total: number;
    by_status: {
      pending: number;
      previewed: number;
      approved: number;
      rejected: number;
      executing: number;
      completed: number;
      failed: number;
      pending_reconciliation: number;
      ambiguous: number;
    };
    latest_at?: string | null;
  } | null;
}

export interface ControlRoomAgentsOpsAgent {
  name?: string | null;
  active?: boolean | null;
  role?: string | null;
  monitor?: boolean | null;
  operationally_ready?: boolean | null;
  operational_tools_count: number;
  last_run?: ControlRoomAgentsOpsRun | null;
  alerts: {
    total: number;
    open: number;
    last_seen_at?: string | null;
  };
}

export interface ControlRoomAgentsOpsPayload {
  generated_at?: string | null;
  summary: {
    agents_total: number;
    active_agents: number;
    monitor_agents: number;
    recent_runs: number;
    failed_recent_runs: number;
    open_agent_alerts: number;
    agent_alerts_total: number;
    configured_engines?: number;
    monte_carlo_simulations?: number;
    bayesian_calibration_states?: number;
    bayesian_calibration_samples?: number;
    decision_orchestrations?: number;
  };
  agents?: ControlRoomAgentsOpsAgent[];
  recent_runs?: ControlRoomAgentsOpsRun[];
  engines?: ControlRoomAgentsOpsEngine[];
  origins?: Array<{ origin?: string | null; count: number }>;
  operational_diagnostics?: Array<{
    diagnostic?: string | null;
    scope?: string | null;
    state_count: number;
    sample_count: number;
    latest_at?: string | null;
    included_in_business_counters?: boolean | null;
  }>;
}

export interface MarketDecisionValidationPayload {
  status: "ready" | "partial" | "insufficient_data";
  source: {
    input_status: string;
    source_mode: string;
    employee_count: number;
    confidence?: number | string | null;
  };
  market_context: {
    provider: string;
    metric_name: string;
    as_of?: string | null;
    unit?: string | null;
    confidence?: number | string | null;
    freshness_status?: string | null;
  };
  simulation: {
    available: boolean;
    output_metric?: string | null;
    p10?: number | null;
    p50?: number | null;
    p90?: number | null;
    updated_at?: string | null;
    market_evidence_count: number;
  };
  bayes: {
    status: string;
    sample_count: number;
    evidence_policy: "evidence_only";
  };
  orchestration: {
    available: boolean;
    problem_type?: string | null;
    action_recommended: boolean;
  };
  policy: {
    recommendation_only: boolean;
    causal_claim: boolean;
    financial_forecast: boolean;
    creates_calibration_observation: boolean;
    automatic_action: boolean;
    external_writeback: boolean;
  };
}

export interface SfGoldWidgetRow {
  label?: string | null;
  company_name?: string | null;
  location_name?: string | null;
  department_name?: string | null;
  value?: string | number | boolean | null;
  count?: number | null;
  headcount?: number | null;
  contractor_count?: number | null;
  risk_factor?: number | null;
  percentage?: number | null;
  rate?: number | null;
  status?: string | null;
  fact?: string | null;
}

export interface SfGoldWidget {
  id?: string | null;
  title?: string | null;
  value?: string | number | boolean | null;
  contractor_count?: number | null;
  risk_factor?: number | null;
  rows?: SfGoldWidgetRow[];
  status?: DataReadiness | SourceState | "ready" | null;
}

export interface SfGoldKpisPayload {
  generated_at?: string | null;
  widgets?: SfGoldWidget[];
}

export interface SfTalentWidget {
  id?: string | null;
  title?: string | null;
  value?: string | number | boolean | null;
  contractor_count?: number | null;
  risk_factor?: number | null;
  status?: DataReadiness | SourceState | "ready" | "partial" | null;
  rows?: SfGoldWidgetRow[];
}

export interface SfTalentSignal {
  id?: string | null;
  severity?: Severity | string | null;
  title?: string | null;
  affected_count?: number;
  recommendation?: string | null;
  status?: string | null;
}

export interface SfTalentBlocker {
  id?: string | null;
  status?: DataReadiness | SourceState | "partial" | null;
  title?: string | null;
}

export interface SfTalentKpisPayload {
  generated_at?: string | null;
  profile?: {
    industry?: string | null;
    company_profile?: string | null;
    decision_mode?: string | null;
    compensation_enabled?: boolean | null;
    write_back_enabled?: boolean | null;
  };
  readiness?: {
    ready_min?: number;
    near_min?: number;
    profiled_employees?: number;
    calculable_employees?: number;
    insufficient_data_employees?: number;
    nine_box_available?: number;
    roles_without_requirements?: number;
    high_severity_signals?: number;
    learning_blockers?: number;
    recruiting_blockers?: number;
    skill_gap_count?: number;
    skill_coverage_pct?: number | null;
    status?: DataReadiness | SourceState | "partial" | null;
    confidence?: number | null;
  };
  widgets?: SfTalentWidget[];
  signals?: SfTalentSignal[];
  blockers?: SfTalentBlocker[];
  workforce_trends?: SfWorkforceTrends | null;
}

export interface SfWorkforceTrends {
  status?: "ready" | "partial" | "waiting_for_data" | string | null;
  kpis: {
    active_headcount: number | null;
    avg_tenure_months: number | null;
    attrition_rate: number | null;
    history_months: number | null;
  };
  series: {
    months: string[];
    headcount: Array<number | null>;
    avg_tenure_months: Array<number | null>;
    attrition_rate: Array<number | null>;
  };
}

export interface SfTalentNineBoxCell {
  box_id?: string | null;
  box_label?: string | null;
  potential_band?: "low" | "medium" | "high" | string | null;
  performance_band?: "low" | "medium" | "high" | string | null;
  movement_action?: string | null;
  display_order: number;
  employee_count: number;
  ready_count: number;
  cpa_real_count?: number;
  reference_count?: number;
  blocked_count: number;
  status?: DataReadiness | SourceState | "ready" | "partial" | null;
}

export interface SfTalentDesempenoRow {
  employee_key?: string | null;
  display_name?: string | null;
  role?: string | null;
  unit?: string | null;
  performance_band_available?: "high" | "medium" | "low" | string | null;
  potential_pending?: boolean | null;
  fit_band?: string | null;
}

export interface SfTalentDesempenoCohort {
  count: number;
  band_counts: { high: number; medium: number; low: number };
  roster: SfTalentDesempenoRow[];
  roster_truncated?: boolean | null;
}

export interface SfTalentNineBoxPayload {
  generated_at?: string | null;
  status: DataReadiness | SourceState | "ready" | "partial";
  totals: {
    employees: number;
    ready: number;
    reference?: number;
    blocked: number;
    cells: number;
  };
  cells?: SfTalentNineBoxCell[];
  desempeno_disponible?: SfTalentDesempenoCohort | null;
  blockers?: SfTalentBlocker[];
  privacy?: {
    roster?: string;
    forbidden_fields?: string[];
    excluded_fields?: string[];
    masked?: boolean | null;
  } | null;
}

export interface SfTalentRosterRow {
  employee_key?: string | null;
  display_name?: string | null;
  role?: string | null;
  unit?: string | null;
  region?: string | null;
  box_label?: string | null;
  performance_band?: string | null;
  performance_band_available?: string | null;
  potential_pending?: boolean | null;
  desempeno_disponible?: boolean | null;
  potential_band?: string | null;
  fit_band?: string | null;
  movement_age_bucket?: string | null;
  data_status?: string | null;
}

export interface SfTalentRosterPayload {
  generated_at?: string | null;
  box?: {
    box_id?: string | null;
    box_label?: string | null;
    potential_band?: string | null;
    performance_band?: string | null;
    movement_action?: string | null;
    display_order: number;
  };
  status: DataReadiness | SourceState | "ready" | "partial";
  count: number;
  roster?: SfTalentRosterRow[];
  blockers?: SfTalentBlocker[];
  privacy?: {
    masked?: boolean;
    excluded_fields?: string[];
  } | null;
}

export interface SfTalentAnomaly {
  id?: string | null;
  severity?: Severity | string | null;
  title?: string | null;
  affected_count: number;
  recommendation?: string | null;
  status?: string | null;
}

export interface SfTalentAnomaliesPayload {
  generated_at?: string | null;
  status: DataReadiness | SourceState | "ready" | "partial";
  summary?: {
    total: number;
    high: number;
    recommendation_only: number;
  };
  items?: SfTalentAnomaly[];
  blockers?: SfTalentBlocker[];
}

export interface SfTalentDiagnosticComponent {
  component?: string | null;
  purpose?: string | null;
  status?: DataReadiness | SourceState | "ready" | "partial" | "available" | null;
  ready_to_extract?: boolean | null;
}

export interface SfTalentMetadataReadinessPayload {
  generated_at?: string | null;
  status: DataReadiness | SourceState | "ready" | "partial";
  summary?: {
    cpa_ready_employees: number;
    cpa_insufficient_employees: number;
    components: number;
    blocked_components: number;
    required_sources_ready: number;
    required_sources_total: number;
  };
  components?: SfTalentDiagnosticComponent[];
  blockers?: Array<{
    status?: string | null;
    title?: string | null;
    detail?: string | null;
  }>;
  source_check?: {
    status?: string | null;
    required_ready: number;
    required_total: number;
  };
}

export interface SfTalentOverviewPayload extends SfTalentKpisPayload {
  nine_box?: {
    status?: DataReadiness | SourceState | "ready" | "partial" | null;
    totals?: SfTalentNineBoxPayload["totals"];
    cells?: SfTalentNineBoxCell[];
    blockers?: SfTalentBlocker[];
  };
  anomalies?: {
    status?: DataReadiness | SourceState | "ready" | "partial" | null;
    summary?: SfTalentAnomaliesPayload["summary"];
    items?: SfTalentAnomaly[];
  };
}

export interface SfDecisionTerm {
  term?: string;
  definition?: string;
  maps_to?: string;
  [key: string]: unknown;
}

export interface SfDecisionEntity {
  entity?: string;
  name?: string;
  display_name?: string;
  description?: string;
  module?: string;
  fields?: string[];
  columns?: string[];
  select_fields?: string[];
  [key: string]: unknown;
}

export interface SfDecisionModelPayload {
  cartridge?: string;
  entities?: SfDecisionEntity[] | Record<string, SfDecisionEntity>;
  server?: {
    entities?: SfDecisionEntity[];
    semantic_model?: {
      vocabulary?: SfDecisionTerm[];
    };
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface ThresholdCandidate extends DetectionThreshold {
  key: string;
  module: string;
  module_id?: string;
  domain: string;
  title?: string;
  item_count: number;
}

export interface ThresholdDraft {
  cartridge_id: string;
  anomaly_type: string;
  metric: string;
  warning_value: string;
  critical_value: string;
  currency: string;
  enabled: boolean;
}

export interface Lesson {
  anomaly_type?: string | null;
  rule?: string | null;
  confidence?: number | null;
  created_at?: string | null;
}

export interface LessonPattern {
  anomaly_type?: string | null;
  count: number;
  avg_confidence?: number | null;
  latest_rule?: string | null;
  last_seen_at?: string | null;
}

export interface LessonApplication {
  lesson_id?: number;
  rule: string;
  applied_at?: string;
  applied_by?: string;
  note?: string;
}

export interface ActivityEntry {
  label?: string | null;
  status?: string | null;
  at?: string | null;
}

export interface ActivityPayload {
  item_id: string;
  activity: ActivityEntry[];
  counts: {
    events: number;
    executions: number;
    decision_actions: number;
    action_runs: number;
    outcomes: number;
    total: number;
  };
}

export interface ControlChecklistItem {
  id: string;
  desc: string;
  owner: string;
  status?: "open" | "in_progress" | "closed" | "blocked" | string;
  st: string;
  impact: string;
  days: number;
  due_at?: string;
  note?: string;
}

export interface Omega {
  investigation: {
    root_cause?: string;
    impact?: string;
    evidence?: Record<string, unknown>;
  };
  options: OmegaOption[];
  decision: {
    decision_id?: number | null;
    status: string;
    label: string;
  };
  execution: {
    status?: string;
    supported_writeback_templates?: string[];
    templates?: ActionTemplate[];
    actions: Array<{ id: string; label: string; done?: boolean; approved: boolean; auto?: boolean }>;
  };
  control: {
    owner?: string;
    cadence?: string;
    status?: string;
    items?: ControlChecklistItem[];
  };
  lessons: {
    rules: string[];
    applied?: LessonApplication[];
  };
  decision_intelligence?: DecisionIntelligence;
  intelligence?: IntelligencePack;
}

export type DecisionMethod =
  | "robust_baseline_v0"
  | "robust_residual_v0"
  | "seasonal_residual_mad_v0"
  | "insufficient_history"
  | "deterministic_guardrail"
  | "dataset_unavailable"
  | "future_reserved_bayesian"
  | "future_reserved_conformal"
  | "future_reserved_state_space";

export type DecisionOptionName = "act_now" | "investigate" | "wait" | "monitor";
export type DecisionRecommendation = DecisionOptionName | "insufficient_data";
export type DecisionLevel = "low" | "medium" | "high" | "unknown";
export type DecisionQualityStatus = "sufficient" | "thin" | "insufficient";

export interface DecisionIntelligence {
  method: DecisionMethod;
  anomaly_probability?: number | null;
  probability_basis: string;
  uncertainty_level: DecisionLevel;
  confidence_interval: { lower?: number | null; upper?: number | null; unit?: string | null };
  expected_impact: { value?: number | null; currency?: string | null; basis: string };
  cost_of_delay: { value_per_day?: number | null; currency?: string | null; basis: string };
  downside_risk: { value?: number | null; currency?: string | null; basis: string };
  value_of_information: { level: DecisionLevel; rationale: string };
  recommended_decision: DecisionRecommendation;
  recommended_next_step: string;
  rationale: string;
  options: Array<{
    option: DecisionOptionName;
    expected_utility?: number | null;
    utility_basis: string;
    risk: DecisionLevel;
    explanation: string;
  }>;
  data_quality: {
    history_points: number;
    minimum_required: number;
    status: DecisionQualityStatus;
    missing_fields: string[];
  };
  calibration?: {
    raw_probability?: number;
    calibrated_probability?: number;
    calibration_applied?: boolean;
    calibration_reason?: string;
    calibration_group?: string | null;
    sample_count?: number;
    posterior_mean?: number | null;
    posterior_alpha?: number | null;
    posterior_beta?: number | null;
  } | null;
  time_series?: Record<string, unknown> | null;
}

export type ControlOrigin =
  | "rule"
  | "generic_gold_signal"
  | "intelligence_signal"
  | "agent_alert"
  | "source_state"
  | "monte_carlo"
  | "bayesian_calibration";

export interface MonteCarloSummary {
  status?: string;
  mode?: string;
  reason?: string;
  source_type?: string;
  source_id?: string;
  seed?: number;
  iterations?: number;
  reproducibility_hash?: string;
  distribution_summary?: {
    p10?: number;
    p50?: number;
    p90?: number;
    probability_loss?: number;
    probability_breach_threshold?: number | null;
    expected_value?: number;
  };
}

export interface BayesianCalibrationSummary {
  status?: string;
  reason?: string;
  group?: string | null;
  sample_count?: number;
  raw_probability?: number | null;
  calibrated_probability?: number | null;
  posterior_mean?: number | null;
  posterior_alpha?: number | null;
  posterior_beta?: number | null;
}

export interface MathProvenance {
  ruleset_version?: string;
  control_origin?: ControlOrigin | string;
  formula?: string;
  input_hash?: string;
  bayesian_calibration?: BayesianCalibrationSummary;
  monte_carlo?: {
    status?: string;
    mode?: string;
    reason?: string;
    seed?: number;
    reproducibility_hash?: string;
  };
}

export interface IntelligencePack {
  decision_intelligence?: DecisionIntelligence;
  baseline?: {
    method?: string;
    actual_value?: number;
    expected_value?: number;
    predicted_value?: number | null;
    prediction_horizon_days?: number | null;
    prediction_method?: string | null;
    sample_count?: number;
    confidence?: number;
    readiness_status?: string;
  };
  signal?: {
    signal_id?: string;
    metric_name?: string;
    deviation_pct?: number;
    signal_type?: string;
    signal_subtype?: string;
    prediction_horizon_days?: number | null;
    predicted_value?: number | null;
    prediction_method?: string | null;
    confidence?: number;
    summary?: string;
    source_dataset?: string;
    source_row_count?: number;
    readiness_status?: string;
    recommendation_only?: boolean;
    decision_intelligence?: DecisionIntelligence;
  };
  evidence_pack?: {
    summary?: string;
    source_dataset?: string;
    source_row_count?: number;
    readiness_status?: string;
    materialized_at?: string;
    items?: Array<{ source_type?: string; source_ref?: string; supports_hypothesis?: string; strength?: number }>;
  };
  hypotheses?: Array<{ title?: string; rationale?: string; confidence?: number }>;
  options?: Array<{ option_id?: string; label?: string; score?: number; impact_expected?: number; score_explanation?: string }>;
  outcome?: { outcome_summary?: string; prediction_error?: number; actual_value?: number; predicted_value?: number } | null;
}

export interface IntelligenceOutcomeDraft {
  option_id?: string;
  action_taken: string;
  actual_value?: number;
  predicted_value?: number;
  outcome_summary?: string;
  learned_rule?: string;
}

export interface AnalysisEvidence {
  analysis_type?: string;
  engine?: string;
  engine_run_id?: string;
  confidence?: number | null;
  p10?: number | string | null;
  p50?: number | string | null;
  p90?: number | string | null;
  metrics?: Record<string, unknown>;
  blockers?: unknown[];
  recommended_option?: Record<string, unknown> | null;
}

export interface PublicDecisionIntelligence {
  method?: string | null;
  anomaly_probability?: number | null;
  uncertainty_level?: string | null;
  recommended_decision?: string | null;
  recommended_next_step?: string | null;
  rationale?: string | null;
  data_quality_status?: string | null;
}

export interface PublicOmegaState {
  status?: string | null;
  label?: string | null;
}

export interface PublicOmega {
  step?: string | null;
  options?: OmegaOption[];
  decision?: PublicOmegaState;
  execution?: PublicOmegaState;
  control?: PublicOmegaState;
  decision_intelligence?: PublicDecisionIntelligence;
}

export interface ControlItem {
  id: string;
  kind: string;
  title?: string | null;
  description?: string | null;
  severity?: Severity | string | null;
  status?: string | null;
  recommendation?: string | null;
  root_cause?: string | null;
  impact?: string | null;
  detected_at?: string | null;
  domain?: string | null;
  module?: string | null;
  cartridge?: string | null;
  entity_label?: string | null;
  contractor_count?: number | null;
  risk_factor?: number | null;
  value?: string | number | boolean | null;
  count?: number | null;
  impact_estimate?: number | null;
  impact_currency?: string | null;
  confidence?: number | null;
  priority_score?: number | null;
  decision_intelligence?: PublicDecisionIntelligence;
  omega?: PublicOmega;
}

export interface ControlAlert extends ControlItem {
  item_id?: string | null;
  alert_type?: string | null;
  advisory?: boolean | null;
  occurrence_count?: number;
  push_ready?: boolean | null;
  message?: string | null;
}

export interface PublicStatusCounts {
  total: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
  open: number;
  in_review: number;
  approved: number;
  dismissed: number;
  resolved: number;
  ready: number;
  partial: number;
  blocked: number;
  empty: number;
  missing: number;
  unavailable: number;
  invalid_schema: number;
  no_permission: number;
}

export interface Dashboard {
  meta: {
    generated_at?: string | null;
    refresh_interval_seconds?: number | null;
    live_mode?: "polling" | string | null;
    source_count: number;
    item_count: number;
    version?: string | null;
    app_env?: string | null;
    execution_mode?: string | null;
    supervised_execution_enabled?: boolean | null;
    external_writeback_enabled?: boolean | null;
    write_back_enabled?: boolean | null;
  };
  period?: string | null;
  omega_steps: Array<{ id?: string | null; label?: string | null }>;
  summary: {
    total_items: number;
    total_anomalies: number;
    control_items: number;
    critical: number;
    attention: number;
    open_decisions: number;
    active_connectors?: number;
    active_modules?: number;
    active_cartridges: number;
    operational_cartridges: number;
    by_severity: PublicStatusCounts;
    source_states: PublicStatusCounts;
    data_readiness: PublicStatusCounts;
    data_ready_sources?: number;
    data_ready_modules?: number;
    partial_modules?: number;
    stub_modules?: number;
  };
  domains: Domain[];
  cartridges: Cartridge[];
  sources: SourceStatus[];
  alerts?: ControlAlert[];
  items: ControlItem[];
}

export interface LessonsPayload {
  lessons: Lesson[];
  summary: {
    total: number;
    recent: Lesson[];
    top_patterns?: LessonPattern[];
  };
}

export interface ActiveContext {
  level: "portfolio" | "domain" | "module";
  title: string;
  eyebrow: string;
  subtitle: string;
  domainLabel?: string;
  moduleId?: string;
  moduleLabel?: string;
}

export interface CatalogDatasetEntry {
  name?: string;
  dataset?: string;
  layer?: string;
  cartridge?: string;
  row_count?: number | null;
  last_refresh?: string | null;
  status?: string;
  data_readiness?: DataReadiness;
  href?: string;
  [key: string]: unknown;
}

export interface CatalogPayload {
  datasets?: Record<string, CatalogDatasetEntry> | CatalogDatasetEntry[];
  relationships?: unknown[];
  [key: string]: unknown;
}
