import type { LineageEdge, LineageNode, LineagePayload } from "@/lib/monitor/types";

export type { LineageEdge, LineageNode, LineagePayload };

export type SemanticType =
  | "identifier"
  | "text"
  | "date"
  | "datetime"
  | "time"
  | "money"
  | "number"
  | "integer"
  | "percent"
  | "boolean"
  | "complex";

export type CatalogClassification = "pii" | "financial" | "confidential";
export type CatalogOrigin = "manual" | "packaged" | "copilot";
export type CatalogCardinality = "1:1" | "1:N" | "N:1" | "N:N";

export interface CatalogColumn {
  name: string;
  type?: string | null;
  description?: string | null;
  description_origin?: CatalogOrigin | null;
  tags?: string[] | null;
  is_key?: boolean | null;
  is_metric?: boolean | null;
  example_values?: unknown;
  semantic_type?: SemanticType | null;
  classifications?: CatalogClassification[] | null;
  classification_origin?: CatalogOrigin | null;
  copilot_confidence?: number | null;
  copilot_basis?: string[] | null;
  stats_redacted?: boolean | null;
  null_rate?: number | null;
  distinct_count?: number | null;
}

export interface CatalogCopilotSummary {
  status?: "ready" | "partial" | "failed" | null;
  profiled_at?: string | null;
  rules_version?: string | null;
  pii_columns?: number | null;
  financial_columns?: number | null;
  relations?: number | null;
}

export interface CatalogDataset {
  layer?: string | null;
  cartridge?: string | null;
  description?: string | null;
  description_origin?: CatalogOrigin | null;
  display_name?: string | null;
  kind?: "dataset" | "bronze_source" | null;
  row_count?: number | null;
  last_refresh?: string | null;
  columns?: CatalogColumn[] | null;
  copilot?: CatalogCopilotSummary | null;
}

export interface CatalogRelationship {
  from_dataset?: string | null;
  from_column?: string | null;
  to_dataset?: string | null;
  to_column?: string | null;
  join_hint?: string | null;
  cardinality?: CatalogCardinality | null;
  origin?: CatalogOrigin | null;
  status?: "active" | "rejected" | "retired" | null;
  confidence?: number | null;
  basis?: string[] | null;
  description?: string | null;
  transform?: string | null;
}

export interface DataCatalogPayload {
  datasets: Record<string, CatalogDataset>;
  relationships: CatalogRelationship[];
  annotations_degraded?: boolean;
}

export interface CatalogFilters {
  layer?: string;
  cartridge?: string;
  tags?: string;
  datasets?: string;
  include_sources?: boolean;
}

export interface AutoProfileInput {
  cartridge?: string;
  include_sources?: boolean;
  since?: string;
}

export interface AutoProfileStatus {
  status: "idle" | "ready" | "working";
  processed: number;
  pending: number;
  stale: number;
  annotation_epoch?: string | null;
  cached?: boolean;
}

export interface CatalogEdgeInput {
  from_dataset: string;
  from_column: string;
  to_dataset: string;
  to_column: string;
}

export interface CatalogEntryInput {
  dataset: string;
  column_name: string;
  description?: string;
  tags?: string[];
  is_key?: boolean;
  is_metric?: boolean;
  example_values?: unknown[];
}

export interface CatalogRelationshipInput {
  from_dataset: string;
  from_column: string;
  to_dataset: string;
  to_column: string;
  join_hint?: string;
  cardinality?: CatalogCardinality;
  description?: string;
  transform?: string;
}

export type BronzeRow = Record<string, unknown>;
export type DatasetRow = Record<string, unknown>;

export interface BronzeQueryInput {
  sql: string;
  limit: number;
  sources?: string[];
}

export interface BronzeQueryPayload {
  columns?: string[];
  rows?: BronzeRow[];
  data?: BronzeRow[];
  result?: BronzeRow[];
  error?: string;
  message?: string;
  [key: string]: unknown;
}
