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

export interface DatasetSummary {
  name: string;
  layer?: string | null;
  cartridge?: string | null;
  source_entity?: string | null;
  column_count?: number | null;
  column_mapping?: Record<string, string> | null;
  row_count?: number | null;
  is_stale?: boolean | null;
  staleness_reason?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  last_refresh?: string | null;
  sources?: string[] | null;
}

export interface DatasetDetail extends DatasetSummary {
  sql?: string | null;
  source_load_date?: string | null;
  source_batch_id?: string | null;
  columns?: Array<Record<string, unknown>> | null;
}

export interface DatasetLineageRow {
  created_at?: string | null;
  source_batch_id?: string | null;
  row_count?: number | null;
  storage_uri?: string | null;
  [key: string]: unknown;
}

export type DataRow = Record<string, unknown>;

export interface SourceSchemaPayload {
  partitions?: {
    partitions?: string[];
    latest?: string | null;
    sql_latest?: string | null;
    [key: string]: unknown;
  };
  preview?: {
    columns?: string[];
    schema?: Array<{ name?: string; type?: string; [key: string]: unknown }>;
    data?: DataRow[];
    rows?: DataRow[];
    result?: DataRow[];
    [key: string]: unknown;
  };
}

export interface LineageNode {
  id: string;
  label?: string | null;
  type?: string | null;
  cartridge?: string | null;
  is_stale?: boolean | null;
  staleness_reason?: string | null;
  row_count?: number | null;
  last_refresh?: string | null;
}

export interface LineageEdge {
  from: string;
  to: string;
  relation?: string | null;
}

export interface LineagePayload {
  nodes: LineageNode[];
  edges: LineageEdge[];
}

export interface VaultConnection {
  conn_id: string;
  base_url?: string | null;
  auth_method?: string | null;
  updated_at?: string | null;
  created_at?: string | null;
  [key: string]: unknown;
}

export interface VaultSecret {
  key: string;
  value?: string | null;
  masked_value?: string | null;
  updated_at?: string | null;
  created_at?: string | null;
  [key: string]: unknown;
}
