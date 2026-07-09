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
  dataset: string;
  cartridge: string;
  connector_id?: string;
  module_id?: string;
  domain: string;
  module: string;
  status: SourceState;
  count: number;
  data_readiness?: DataReadiness;
  operationally_ready?: boolean;
  readiness_reason?: string;
  readiness_blockers?: string[];
  contract_warnings?: string[];
  error?: string;
  checked_at?: string;
}

export interface Kpi {
  label: string;
  value: string | number;
  tone: "neutral" | "attention";
  bad?: boolean;
}

export interface DomainModule {
  id: string;
  connector_id?: string;
  label: string;
  domain: string;
  accent: string;
  description?: string;
  item_count: number;
  critical_count: number;
  source_status: SourceRollup;
  data_readiness?: DataReadiness;
  operationally_ready?: boolean;
  kpis: Kpi[];
}

export interface Domain {
  id: string;
  label: string;
  accent: string;
  item_count: number;
  critical_count: number;
  cartridge_count: number;
  modules: DomainModule[];
}

export interface Cartridge {
  id: string;
  connector_id?: string;
  connector_label?: string;
  label: string;
  domain: string;
  accent: string;
  description?: string;
  status: string;
  current_step?: string;
  active: boolean;
  operational: boolean;
  item_count: number;
  critical_count: number;
  source_status: SourceRollup;
  data_readiness?: DataReadiness;
  operationally_ready?: boolean;
  datasets: SourceStatus[];
}

export interface OmegaOption {
  id: string;
  label: string;
  action?: string;
  money?: string;
  time?: string;
  score: number;
  risk: string;
  auto?: boolean;
  recommendation: string;
  selected: boolean;
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
  label: string;
  value?: string | number | null;
  currency?: string;
  unit?: string;
  points?: number;
}

export interface ImpactPayload {
  status?: string;
  estimate?: number | null;
  currency?: string;
  confidence?: number | null;
  priority_score?: number | null;
  formula?: string;
  explanation?: string;
  drivers?: ImpactDriver[];
  [key: string]: unknown;
}

export interface DetectionThreshold {
  id?: number;
  cartridge_id: string;
  anomaly_type: string;
  metric: string;
  warning_value?: number | null;
  critical_value?: number | null;
  currency?: string;
  source?: "workspace" | "default" | string;
  enabled?: boolean;
  metadata?: Record<string, unknown>;
}

export interface ThresholdPayload {
  thresholds: DetectionThreshold[];
  summary?: {
    total: number;
    active: number;
    disabled: number;
  };
}

export interface ControlRoomAgentsOpsRun {
  id: number;
  agent_id: string;
  agent_slug: string;
  agent_name: string;
  cartridge_id: string;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  tool_count: number;
  tools: string[];
  error?: string | null;
}

export interface ControlRoomAgentsOpsConfiguredEngine {
  engine: string;
  enabled: boolean;
  source_type?: string | null;
  source_id?: string | null;
  calibration_group?: string | null;
  mode?: string | null;
}

export interface ControlRoomAgentsOpsEngine {
  engine: string;
  configured: number;
  evidence_count: number;
  sample_count?: number;
  latest_at?: string | null;
  status: "ready" | "configured" | "missing" | string;
  executions?: {
    engine: string;
    total: number;
    by_status: Record<string, number>;
    latest_at?: string | null;
  };
}

export interface ControlRoomAgentsOpsAgent {
  id: string;
  cartridge_id: string;
  slug: string;
  name: string;
  active: boolean;
  role: string;
  monitor: boolean;
  operationally_ready?: boolean;
  schedule?: Record<string, unknown>;
  monitor_contract?: Record<string, unknown>;
  configured_engines?: ControlRoomAgentsOpsConfiguredEngine[];
  allowed_tools: string[];
  operational_tools_count?: number;
  last_run?: ControlRoomAgentsOpsRun | null;
  alerts: {
    total: number;
    open: number;
    last_seen_at?: string | null;
  };
}

export interface ControlRoomAgentsOpsPayload {
  generated_at?: string;
  tenant?: string | null;
  active_workspace?: string;
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
  agents: ControlRoomAgentsOpsAgent[];
  recent_runs: ControlRoomAgentsOpsRun[];
  engines?: ControlRoomAgentsOpsEngine[];
  tools_used: Array<{ tool: string; count: number }>;
  origins: Array<{ origin: string; count: number }>;
}

export interface SfGoldWidgetRow {
  label?: string;
  id?: string | null;
  headcount?: number;
  [key: string]: unknown;
}

export interface SfGoldWidget {
  id: string;
  title: string;
  value: number | null;
  dataset: string;
  href?: string;
  rows: SfGoldWidgetRow[];
  status?: DataReadiness | SourceState | "ready";
  error?: string | null;
}

export interface SfGoldKpisPayload {
  generated_at?: string;
  connection_id?: string;
  tenant_id?: string;
  workspace_id?: string;
  widgets: SfGoldWidget[];
}

export interface SfTalentWidget {
  id: string;
  title: string;
  value: number | null;
  dataset: string;
  href?: string;
  status?: DataReadiness | SourceState | "ready" | "partial";
  detail?: string;
  rows?: SfGoldWidgetRow[];
}

export interface SfTalentSignal {
  id: string;
  type: string;
  severity: Severity | string;
  title: string;
  affected_count?: number;
  recommendation?: string;
  status?: string;
}

export interface SfTalentBlocker {
  id: string;
  status: DataReadiness | SourceState | "partial";
  title: string;
  detail: string;
  items?: string[];
}

export interface SfTalentKpisPayload {
  generated_at?: string;
  connection_id?: string;
  tenant_id?: string;
  workspace_id?: string;
  profile: {
    industry: string;
    company_profile: string;
    wisdom_bit: string;
    decision_mode: string;
    compensation_enabled: boolean;
    write_back_enabled: boolean;
  };
  readiness: {
    ready_min: number;
    near_min: number;
    profiled_employees: number;
    calculable_employees: number;
    insufficient_data_employees: number;
    nine_box_available: number;
    status: DataReadiness | SourceState | "partial";
    source_mode?: string;
    readiness_status?: string;
    confidence?: number | null;
  };
  widgets: SfTalentWidget[];
  signals: SfTalentSignal[];
  blockers: SfTalentBlocker[];
  /** Fase 3 P0: fuente unica de plantilla/antiguedad/rotacion/historia + series. */
  workforce_trends?: SfWorkforceTrends;
}

export interface SfWorkforceTrends {
  status: "ready" | "partial" | "waiting_for_data" | string;
  datasets?: Record<string, string>;
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
  box_id: string;
  box_label: string;
  potential_band: "low" | "medium" | "high" | string;
  performance_band: "low" | "medium" | "high" | string;
  movement_action: string;
  display_order: number;
  employee_count: number;
  ready_count: number;
  cpa_real_count?: number;
  reference_count?: number;
  blocked_count: number;
  status: DataReadiness | SourceState | "ready" | "partial";
  href?: string;
}

export interface SfTalentNineBoxPayload {
  generated_at?: string;
  connection_id?: string;
  tenant_id?: string;
  workspace_id?: string;
  dataset: string;
  status: DataReadiness | SourceState | "ready" | "partial";
  totals: {
    employees: number;
    ready: number;
    reference?: number;
    blocked: number;
    cells: number;
  };
  cells: SfTalentNineBoxCell[];
  blockers: SfTalentBlocker[];
  privacy?: {
    roster?: string;
    forbidden_fields?: string[];
  };
}

export interface SfTalentRosterRow {
  employee_key: string;
  display_name: string;
  role: string;
  unit: string;
  region: string;
  readiness_status: string;
  box_id: string;
  box_label: string;
  performance_band: string;
  potential_band: string;
  fit_band: string;
  movement_age_bucket: string;
  data_status: string;
}

export interface SfTalentRosterPayload {
  generated_at?: string;
  connection_id?: string;
  tenant_id?: string;
  workspace_id?: string;
  dataset: string;
  box: {
    box_id: string;
    box_label: string;
    potential_band: string;
    performance_band: string;
    movement_action: string;
    display_order: number;
  };
  status: DataReadiness | SourceState | "ready" | "partial";
  count: number;
  roster: SfTalentRosterRow[];
  blockers: SfTalentBlocker[];
  privacy?: {
    masked?: boolean;
    excluded_fields?: string[];
  };
}

export interface SfTalentAnomaly {
  id: string;
  type: string;
  severity: Severity | string;
  title: string;
  detail: string;
  affected_count: number;
  recommendation: string;
  status: string;
  method: string;
  preview_available: boolean;
}

export interface SfTalentAnomaliesPayload {
  generated_at?: string;
  connection_id?: string;
  tenant_id?: string;
  workspace_id?: string;
  dataset: string;
  status: DataReadiness | SourceState | "ready" | "partial";
  summary: {
    total: number;
    high: number;
    recommendation_only: number;
  };
  items: SfTalentAnomaly[];
  blockers: SfTalentBlocker[];
}

export interface SfTalentMetadataEntity {
  id: string;
  kb: string;
  entity: string;
  odata_entity?: string | null;
  required_for: string;
  status: DataReadiness | SourceState | "ready" | "partial";
  blockers: string[];
  fields_found?: string[];
  fields_missing?: string[];
  ready_to_extract?: boolean;
  live_status?: string | null;
  live_selected_entity?: string | null;
  live_candidates?: Array<Record<string, unknown>>;
}

export interface SfTalentExtractionTarget {
  component: string;
  component_label?: string;
  component_code?: string;
  required?: boolean;
  entity: string;
  odata_entity?: string;
  status?: string;
  ready_to_extract?: boolean;
  sample_status?: string;
  fields_present?: string[];
  fields_found?: string[];
  fields_missing?: string[];
}

export interface SfTalentMetadataReadinessPayload {
  generated_at?: string;
  connection_id?: string;
  tenant_id?: string;
  workspace_id?: string;
  status: DataReadiness | SourceState | "ready" | "partial";
  summary: {
    cpa_ready_employees: number;
    cpa_insufficient_employees: number;
    entities: number;
    blocked_entities: number;
    live_required_ready?: number;
    live_required_total?: number;
    live_status?: string;
  };
  entities: SfTalentMetadataEntity[];
  blockers: SfTalentBlocker[];
  live_preflight?: Record<string, unknown> & {
    extraction_targets?: SfTalentExtractionTarget[];
  };
}

export interface SfTalentOverviewPayload extends SfTalentKpisPayload {
  nine_box: {
    status?: DataReadiness | SourceState | "ready" | "partial";
    totals?: SfTalentNineBoxPayload["totals"];
    cells: SfTalentNineBoxCell[];
    blockers: SfTalentBlocker[];
  };
  anomalies: {
    status?: DataReadiness | SourceState | "ready" | "partial";
    summary?: SfTalentAnomaliesPayload["summary"];
    items: SfTalentAnomaly[];
  };
  metadata_readiness: {
    status?: DataReadiness | SourceState | "ready" | "partial";
    summary?: SfTalentMetadataReadinessPayload["summary"];
    entities: SfTalentMetadataEntity[];
  };
}

export interface SfTalentActionPreviewPayload {
  generated_at?: string;
  connection_id?: string;
  tenant_id?: string;
  workspace_id?: string;
  status: "preview_only" | string;
  action_id: string;
  box_id?: string | null;
  title?: string;
  severity?: Severity | string;
  affected_count: number;
  recommendation?: string;
  method?: string;
  recommendation_only: boolean;
  write_back_enabled: boolean;
  compensation_enabled: boolean;
  requires_approval: boolean;
  external_mutations: unknown[];
  steps: Array<{
    id: string;
    label: string;
    status: string;
  }>;
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
  id?: number;
  item_id: string;
  cartridge_id: string;
  anomaly_type: string;
  rule: string;
  source_decision_id?: number | null;
  confidence?: number | null;
  metadata?: Record<string, unknown>;
  created_at?: string;
}

export interface LessonPattern {
  cartridge_id: string;
  anomaly_type: string;
  count: number;
  avg_confidence?: number | null;
  latest_rule?: string;
  last_seen_at?: string;
}

export interface LessonApplication {
  lesson_id?: number;
  rule: string;
  applied_at?: string;
  applied_by?: string;
  note?: string;
}

export interface ActivityEntry {
  id: string;
  kind: "event" | "execution" | "decision_action" | "action_run" | "outcome";
  type: string;
  label: string;
  status?: string;
  actor?: string;
  at?: string;
  metadata?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: string | null;
}

export interface ActivityPayload {
  item_id: string;
  activity: ActivityEntry[];
  counts: {
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

export interface ControlItem {
  id: string;
  kind: "anomaly" | "control_item" | "source_state" | "intelligence_signal" | "agent_alert";
  domain: string;
  module: string;
  module_id?: string;
  cartridge: string;
  connector_id?: string;
  source_dataset: string;
  entity_kind: string;
  entity_id: string;
  entity_label: string;
  anomaly_type: string;
  severity: Severity;
  severity_weight: number;
  title: string;
  description: string;
  detected_at: string;
  recommendation: string;
  root_cause?: string;
  impact?: string;
  sql?: string;
  status: string;
  decision_id?: number | null;
  selected_option_id?: string;
  execution_status?: string;
  impact_estimate?: number | null;
  impact_currency?: string;
  confidence?: number | null;
  priority_score?: number | null;
  priority?: {
    score: number;
    band: Severity;
    formula?: string;
    drivers?: ImpactDriver[];
  };
  control_origin?: ControlOrigin | string | null;
  capabilities?: Record<string, unknown>;
  math_provenance?: MathProvenance;
  monte_carlo?: MonteCarloSummary;
  bayesian_calibration?: BayesianCalibrationSummary;
  impact_drivers?: ImpactDriver[];
  thresholds_applied?: DetectionThreshold[];
  related_lessons?: Lesson[];
  lesson_count?: number;
  lesson_applications?: LessonApplication[];
  action_templates?: ActionTemplate[];
  decision_intelligence?: DecisionIntelligence;
  intelligence?: IntelligencePack;
  analysis_type?: string | null;
  engine?: string | null;
  engine_run_id?: string | null;
  analysis_evidence?: AnalysisEvidence;
  omega: Omega;
}

export interface ControlAlert {
  id: string;
  item_id: string;
  alert_type: string;
  source?: string;
  advisory?: boolean;
  agent_id?: string | null;
  agent_run_id?: string | number | null;
  analysis_type?: string | null;
  engine?: string | null;
  engine_run_id?: string | null;
  analysis_evidence?: AnalysisEvidence;
  deduped?: boolean;
  occurrence_count?: number;
  hypothesis?: string | null;
  expected_outcome?: string | null;
  severity: Severity;
  priority_score: number;
  domain: string;
  module: string;
  module_id?: string;
  cartridge: string;
  connector_id?: string;
  source_dataset: string;
  title: string;
  message: string;
  status: string;
  owner?: string | null;
  note?: string | null;
  snoozed_until?: string | null;
  threshold_state?: string;
  lesson_count?: number;
  impact_estimate?: number | null;
  impact_currency?: string;
  recommended_action?: string;
  drivers?: ImpactDriver[];
  push_ready?: boolean;
  delivery?: {
    status: string;
    channels?: string[];
    reason?: string;
  };
  created_at?: string;
}

export interface Dashboard {
  meta?: {
    generated_at?: string;
    refresh_interval_seconds?: number;
    live_mode?: "polling" | string;
    version?: string;
    app_env?: string;
    execution_mode?: string;
    supervised_execution_enabled?: boolean;
    external_writeback_enabled?: boolean;
    write_back_enabled?: boolean;
  };
  workspace: {
    workspace_id: string;
  };
  period: string;
  omega_steps: Array<{ id: string; label: string }>;
  summary: {
    total_items: number;
    critical: number;
    attention: number;
    open_decisions: number;
    active_connectors?: number;
    active_modules?: number;
    active_cartridges: number;
    operational_cartridges: number;
    source_states: Partial<Record<SourceState, number>>;
    data_readiness?: Partial<Record<DataReadiness, number>>;
    data_ready_sources?: number;
    data_ready_modules?: number;
    partial_modules?: number;
    stub_modules?: number;
    cycle_counts?: Record<string, number>;
    thresholds?: {
      active: number;
      total: number;
      items_with_thresholds?: number;
    };
    lessons?: {
      total: number;
      recent: Lesson[];
      by_cartridge?: Record<string, number>;
      top_patterns?: LessonPattern[];
    };
    alerts?: {
      total: number;
      critical: number;
      high: number;
      medium: number;
      low: number;
      push_ready: number;
      by_type?: Record<string, number>;
      top?: ControlAlert[];
    };
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
    by_cartridge?: Record<string, number>;
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
