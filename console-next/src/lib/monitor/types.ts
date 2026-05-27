export type JobStatus = "queued" | "running" | "done" | "failed" | "success" | "error" | "unknown" | string;

export interface JobRun {
  job_id: string;
  status: JobStatus;
  tool?: string | null;
  message?: string | null;
  args?: Record<string, unknown> | null;
  result?: Record<string, unknown> | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface JobLogLine {
  entity?: string | null;
  level?: string | null;
  message?: string | null;
  detail?: string | null;
  ts?: string | null;
}

export interface PipelineNode {
  name: string;
  layer: string;
  row_count?: number | null;
  last_refresh?: string | null;
  status?: string | null;
}

export interface PipelineLastRun {
  source?: string | null;
  job_id?: string | null;
  dag_id?: string | null;
  dag_run_id?: string | null;
  status?: string | null;
  mode?: string | null;
  triggered_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  duration_sec?: number | null;
  message?: string | null;
  error?: string | null;
}

export interface PipelineEntity {
  entity: string;
  cartridge: string;
  modes: string[];
  watermark?: string | null;
  last_run?: PipelineLastRun | null;
  last_job?: PipelineLastRun | null;
  bronze: {
    source: string;
    latest_date?: string | null;
    record_count?: number | null;
    status: string;
  };
  silver: PipelineNode[];
  gold: PipelineNode[];
}

export interface FreshnessEntity {
  entity: string;
  watermark_value?: string | null;
  watermark_updated_at?: string | null;
  last_run_status?: string | null;
  last_run_at?: string | null;
  age_seconds?: number | null;
}

export interface SemanticEntity {
  name?: string;
  entity?: string;
  description?: string | null;
  layer?: string | null;
  watermark_field?: string | null;
  watermark?: string | null;
  last_watermark?: string | null;
  fields?: unknown[];
  [key: string]: unknown;
}

export interface SemanticPayload {
  cartridge?: string;
  server?: Record<string, unknown>;
  entities?: Record<string, SemanticEntity[]> | SemanticEntity[];
}
