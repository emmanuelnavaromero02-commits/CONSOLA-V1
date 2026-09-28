import type { NlParseResult } from "./nl-parse";
import { KIND_LABELS, OPERATOR_LABELS, operatorAllowed, operatorArity, type ColumnKind, type ExplorerOp } from "./operators";

export const BRONZE_ROW_CAP = 2000;
export const DATASET_ROW_CAP = 10000;
export const DEFAULT_ROWS = 50;
export const MAX_FILTERS = 20;
export const MAX_SORT = 3;
export const MAX_IN_VALUES = 100;

export interface ExplorerColumn {
  name: string;
  type: string;
  kind: ColumnKind;
}

export type ExplorerSource =
  | { kind: "bronze"; cartridge: string; entity: string }
  | { kind: "dataset"; name: string };

export interface ExplorerFilterDraft {
  id: string;
  column: string;
  op: ExplorerOp;
  value: string;
  valueTo: string;
}

export interface ExplorerSortDraft {
  column: string;
  direction: "asc" | "desc";
}

export interface ExplorerSpec {
  columns: string[];
  filters: ExplorerFilterDraft[];
  sort: ExplorerSortDraft[];
  limit: number;
  latestOnly: boolean;
}

export type ExploreApiFilter =
  | { column: string; op: ExplorerOp }
  | { column: string; op: ExplorerOp; value: string }
  | { column: string; op: ExplorerOp; values: string[] };

export interface ExploreRequest {
  source: ExplorerSource;
  columns: string[];
  filters: ExploreApiFilter[];
  sort: ExplorerSortDraft[];
  limit: number;
  latest_only: boolean;
  execute: boolean;
}

export const EMPTY_SPEC: ExplorerSpec = { columns: [], filters: [], sort: [], limit: DEFAULT_ROWS, latestOnly: false };

let filterSeq = 0;

export function newFilterId(): string {
  filterSeq += 1;
  return `f${filterSeq}`;
}

export function sourceKey(source: ExplorerSource | null | undefined): string {
  if (!source) return "";
  return source.kind === "bronze" ? `raw/${source.cartridge}/${source.entity}` : `gold/${source.name}`;
}

export function sourceFromKey(key: string): ExplorerSource | null {
  const bronze = /^raw\/([A-Za-z_][A-Za-z0-9_]*)\/([A-Za-z_][A-Za-z0-9_]*)$/.exec(key.trim());
  if (bronze) return { kind: "bronze", cartridge: bronze[1], entity: bronze[2] };
  const gold = /^gold\/([A-Za-z_][A-Za-z0-9_]*)$/.exec(key.trim());
  if (gold) return { kind: "dataset", name: gold[1] };
  return null;
}

export function rowCapFor(source: ExplorerSource | null | undefined): number {
  return source?.kind === "dataset" ? DATASET_ROW_CAP : BRONZE_ROW_CAP;
}

export function clampRows(value: unknown, cap: number): number {
  const parsed = typeof value === "number" ? value : Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed)) return Math.min(DEFAULT_ROWS, cap);
  return Math.min(cap, Math.max(1, Math.trunc(parsed)));
}

export function splitListValue(value: string): string[] {
  return [...new Set(value.split(/[,;\n]/).map((item) => item.trim()).filter(Boolean))];
}

export function emptyFilter(columns: ExplorerColumn[], column?: string): ExplorerFilterDraft {
  const chosen = columns.find((item) => item.name === column) ?? columns[0];
  const kind = chosen?.kind ?? "text";
  const op: ExplorerOp = kind === "text" ? "contains" : kind === "other" ? "is_not_empty" : "eq";
  return { id: newFilterId(), column: chosen?.name ?? "", op, value: "", valueTo: "" };
}

function columnLabel(column: string): string {
  return column ? `«${column}»` : "sin columna";
}

export function specProblems(spec: ExplorerSpec, columns: ExplorerColumn[]): string[] {
  const byName = new Map(columns.map((column) => [column.name, column]));
  const problems: string[] = [];
  if (spec.filters.length > MAX_FILTERS) problems.push(`Máximo ${MAX_FILTERS} filtros.`);
  if (spec.sort.length > MAX_SORT) problems.push(`Máximo ${MAX_SORT} criterios de orden.`);
  spec.filters.forEach((filter, index) => {
    const label = `Filtro ${index + 1}`;
    const column = byName.get(filter.column);
    if (!column) {
      problems.push(`${label}: elige una columna.`);
      return;
    }
    if (!operatorAllowed(filter.op, column.kind)) {
      problems.push(
        `${label}: «${OPERATOR_LABELS[filter.op]}» no aplica a ${columnLabel(column.name)} (${KIND_LABELS[column.kind]}).`,
      );
      return;
    }
    const arity = operatorArity(filter.op);
    if (arity === "one" && !filter.value.trim()) problems.push(`${label}: escribe un valor.`);
    if (arity === "two" && (!filter.value.trim() || !filter.valueTo.trim())) {
      problems.push(`${label}: «está entre» necesita dos valores.`);
    }
    if (arity === "many") {
      const values = splitListValue(filter.value);
      if (!values.length) problems.push(`${label}: escribe al menos un valor.`);
      if (values.length > MAX_IN_VALUES) problems.push(`${label}: máximo ${MAX_IN_VALUES} valores.`);
    }
  });
  const seen = new Set<string>();
  spec.sort.forEach((sort, index) => {
    if (!byName.has(sort.column)) problems.push(`Orden ${index + 1}: elige una columna.`);
    if (seen.has(sort.column)) problems.push(`Orden ${index + 1}: ${columnLabel(sort.column)} está repetida.`);
    seen.add(sort.column);
  });
  return problems;
}

function apiFilter(filter: ExplorerFilterDraft): ExploreApiFilter {
  const arity = operatorArity(filter.op);
  if (arity === "none") return { column: filter.column, op: filter.op };
  if (arity === "two") return { column: filter.column, op: filter.op, values: [filter.value.trim(), filter.valueTo.trim()] };
  if (arity === "many") return { column: filter.column, op: filter.op, values: splitListValue(filter.value) };
  return { column: filter.column, op: filter.op, value: filter.value.trim() };
}

export function buildExploreRequest(
  source: ExplorerSource,
  spec: ExplorerSpec,
  { execute }: { execute: boolean },
): ExploreRequest {
  const cap = rowCapFor(source);
  return {
    source,
    columns: spec.columns,
    filters: spec.filters.map(apiFilter),
    sort: spec.sort.map((sort) => ({ column: sort.column, direction: sort.direction })),
    limit: clampRows(spec.limit, cap),
    latest_only: source.kind === "bronze" ? spec.latestOnly : false,
    execute,
  };
}

export function specIsEmpty(spec: ExplorerSpec): boolean {
  return !spec.columns.length && !spec.filters.length && !spec.sort.length && !spec.latestOnly;
}

export function mergeParsedSpec(spec: ExplorerSpec, result: NlParseResult): ExplorerSpec {
  return {
    ...spec,
    filters: result.filters.length ? result.filters.map((filter) => ({ id: newFilterId(), ...filter })) : spec.filters,
    sort: result.sort.length ? result.sort : spec.sort,
    limit: result.limit ?? spec.limit,
    latestOnly: result.latestOnly || spec.latestOnly,
  };
}
