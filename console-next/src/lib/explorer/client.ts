import { api } from "@/lib/api";

import type { ColumnKind } from "./operators";
import type { ExploreRequest, ExplorerColumn, ExplorerSource } from "./spec";

export type ExploreCell = string | number | boolean | null;

export interface ExploreResponse {
  source: string;
  source_kind: "bronze" | "dataset";
  available_columns: ExplorerColumn[];
  executed: boolean;
  columns: string[];
  rows: ExploreCell[][];
  row_count: number;
  limit: number | null;
  truncated: boolean;
  sql_display: string;
  sql_definition: string | null;
  sources: string[];
}

const KINDS = new Set<ColumnKind>(["text", "number", "temporal", "boolean", "other"]);

function normalizeColumns(value: unknown): ExplorerColumn[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const record = item as Record<string, unknown>;
    const name = typeof record.name === "string" ? record.name : "";
    if (!name) return [];
    const kind = KINDS.has(record.kind as ColumnKind) ? (record.kind as ColumnKind) : "other";
    return [{ name, type: typeof record.type === "string" ? record.type : "", kind }];
  });
}

export function normalizeExploreResponse(payload: unknown): ExploreResponse {
  const record = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  const columns = Array.isArray(record.columns) ? record.columns.map(String) : [];
  const rows = Array.isArray(record.rows)
    ? record.rows.filter((row): row is ExploreCell[] => Array.isArray(row))
    : [];
  return {
    source: String(record.source ?? ""),
    source_kind: record.source_kind === "dataset" ? "dataset" : "bronze",
    available_columns: normalizeColumns(record.available_columns),
    executed: record.executed === true,
    columns,
    rows,
    row_count: typeof record.row_count === "number" ? record.row_count : rows.length,
    limit: typeof record.limit === "number" ? record.limit : null,
    truncated: record.truncated === true,
    sql_display: typeof record.sql_display === "string" ? record.sql_display : "",
    sql_definition: typeof record.sql_definition === "string" ? record.sql_definition : null,
    sources: Array.isArray(record.sources) ? record.sources.map(String) : [],
  };
}

export async function exploreData(request: ExploreRequest): Promise<ExploreResponse> {
  const { data } = await api.post<unknown>("/api/data/explore", request);
  return normalizeExploreResponse(data);
}

export async function describeSource(source: ExplorerSource): Promise<ExploreResponse> {
  return exploreData({ source, columns: [], filters: [], sort: [], limit: 1, latest_only: false, execute: false });
}

export function exploreRowsAsRecords(response: Pick<ExploreResponse, "columns" | "rows"> | null | undefined): Array<Record<string, unknown>> {
  if (!response) return [];
  return response.rows.map((row) => Object.fromEntries(response.columns.map((column, index) => [column, row[index] ?? null])));
}
