import { toCsv } from "@/lib/csv";

import { formatCount, plural } from "./format";

export type SortDirection = "asc" | "desc";

const NUMERIC = /^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$/;

export function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function isBlank(value: unknown): boolean {
  return value === null || value === undefined || (typeof value === "string" && !value.trim());
}

function numericValue(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && NUMERIC.test(value.trim())) return Number(value.trim());
  return null;
}

function compareValues(a: unknown, b: unknown): number {
  const left = numericValue(a);
  const right = numericValue(b);
  if (left !== null && right !== null) return left - right;
  return formatCell(a).localeCompare(formatCell(b), "es", { numeric: true });
}

export function sortRows<T extends Record<string, unknown>>(rows: readonly T[], key: string | null, dir: SortDirection): T[] {
  if (!key) return [...rows];
  const sign = dir === "asc" ? 1 : -1;
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => {
      const va = a.row[key];
      const vb = b.row[key];
      const blankA = isBlank(va);
      const blankB = isBlank(vb);
      if (blankA || blankB) return blankA === blankB ? a.index - b.index : blankA ? 1 : -1;
      return compareValues(va, vb) * sign || a.index - b.index;
    })
    .map((item) => item.row);
}

export function pageCount(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(Math.max(0, total) / Math.max(1, pageSize)));
}

export function paginate<T>(rows: readonly T[], page: number, pageSize: number): T[] {
  const size = Math.max(1, pageSize);
  return rows.slice(page * size, (page + 1) * size);
}

export function tableSummary({
  start,
  end,
  loaded,
  total,
}: {
  start: number;
  end: number;
  loaded: number;
  total?: number | null;
}): string {
  if (loaded <= 0) return "Sin registros";
  const range = `Mostrando ${formatCount(start)}–${formatCount(end)}`;
  if (typeof total === "number" && Number.isFinite(total) && total > loaded) {
    return `${range} de ${plural(total, "registro", "registros")} · ${plural(loaded, "cargado", "cargados")} en la vista previa`;
  }
  return `${range} de ${plural(loaded, "registro", "registros")}`;
}

export function rowsToCsv(keys: readonly string[], rows: ReadonlyArray<Record<string, unknown>>): string {
  return toCsv([[...keys], ...rows.map((row) => keys.map((key) => formatCell(row[key])))]);
}

export function csvFilename(name: string | null | undefined): string {
  const base = String(name ?? "")
    .trim()
    .replace(/\.csv$/i, "")
    .replace(/[^A-Za-z0-9._-]+/g, "_")
    .replace(/^[._-]+|[._-]+$/g, "")
    .slice(0, 80);
  return `${base || "vista-previa"}.csv`;
}
