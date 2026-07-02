export type OperationalRecord = Record<string, unknown>;

export interface OperationalList<T = OperationalRecord> {
  items: T[];
  total?: number;
  [key: string]: unknown;
}

export interface ScenarioSummary extends OperationalRecord {
  id?: string;
  simulation_id?: string;
  source_id?: string;
  status?: string;
  created_at?: string;
  finished_at?: string | null;
  probability?: number;
  expected_value?: number;
}

export interface ConfidenceSummary extends OperationalRecord {
  status?: string;
  outcomes_count?: number;
  min_outcomes_required?: number;
  groups?: OperationalRecord[];
}

export interface DecisionPlanSummary extends OperationalRecord {
  id?: string;
  orchestration_id?: string;
  title?: string;
  status?: string;
  created_at?: string;
  source_id?: string;
}

export interface HistoricalValidationSummary extends OperationalRecord {
  id?: string;
  backtest_id?: string;
  metric?: string;
  status?: string;
  created_at?: string;
  result_count?: number;
}

export interface OperationalRunSummary extends OperationalRecord {
  id?: string;
  run_id?: string;
  status?: string;
  started_at?: string;
  finished_at?: string | null;
  source?: string;
}

export interface DecisionPlanRequest {
  source_type: "control_room_item" | "agent_alert" | "intelligence_signal" | "wisdom_bit" | "manual_fixture";
  source_id: string;
  title?: string;
  description?: string;
  metrics?: Record<string, unknown>;
  entities?: Record<string, unknown>[];
  time_horizon?: string;
  constraints?: Record<string, unknown>;
  evidence_refs?: Record<string, unknown>[];
}

export interface HistoricalValidationRequest {
  source_dataset?: string;
  metric: string;
  start_period?: string;
  end_period?: string;
  mode?: "historical_replay" | "outcome_linked" | "fixture_validation";
  labels_required?: number;
  prediction_threshold?: number;
  historical_label_robust_z?: number;
  limit?: number;
  result_limit?: number;
}
