import type { LineageEdge, LineageNode, LineagePayload } from "@/lib/monitor/types";

export type { LineageEdge, LineageNode, LineagePayload };

export interface CatalogColumn {
  name: string;
  type?: string | null;
  description?: string | null;
  tags?: string[] | null;
  is_key?: boolean | null;
  is_metric?: boolean | null;
  example_values?: unknown;
}

export interface CatalogDataset {
  layer?: string | null;
  cartridge?: string | null;
  description?: string | null;
  row_count?: number | null;
  last_refresh?: string | null;
  columns?: CatalogColumn[] | null;
}

export interface CatalogRelationship {
  from_dataset?: string | null;
  from_column?: string | null;
  to_dataset?: string | null;
  to_column?: string | null;
  join_hint?: string | null;
  description?: string | null;
  transform?: string | null;
}

export interface DataCatalogPayload {
  datasets: Record<string, CatalogDataset>;
  relationships: CatalogRelationship[];
}

export interface CatalogFilters {
  layer?: string;
  cartridge?: string;
  tags?: string;
  datasets?: string;
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
