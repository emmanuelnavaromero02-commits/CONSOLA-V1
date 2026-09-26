"use client";

import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight, Copy, Download } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { copyText } from "@/lib/clipboard";
import { downloadText } from "@/lib/csv";
import {
  csvFilename,
  formatCell,
  pageCount,
  paginate,
  rowsToCsv,
  sortRows,
  tableSummary,
  type SortDirection,
} from "@/lib/studio/table";
import { cn } from "@/lib/utils";

import { buttonClass } from "./ui";

const SORT_LABEL: Record<SortDirection, "ascending" | "descending"> = { asc: "ascending", desc: "descending" };

export function DataTable({
  columns,
  rows,
  caption,
  maxHeight = "max-h-[420px]",
  total = null,
  pageSize = 25,
  exportName,
}: {
  columns: string[];
  rows: Array<Record<string, unknown>>;
  caption?: string;
  maxHeight?: string;
  total?: number | null;
  pageSize?: number;
  exportName?: string;
}) {
  const keys = useMemo(
    () => (columns.length ? columns : [...new Set(rows.slice(0, 25).flatMap((row) => Object.keys(row)))]),
    [columns, rows],
  );
  const [sort, setSort] = useState<{ key: string; dir: SortDirection } | null>(null);
  const [page, setPage] = useState(0);
  const sorted = useMemo(() => sortRows(rows, sort?.key ?? null, sort?.dir ?? "asc"), [rows, sort]);
  const pages = pageCount(sorted.length, pageSize);
  const current = Math.min(page, pages - 1);
  const visible = paginate(sorted, current, pageSize);
  const start = visible.length ? current * pageSize + 1 : 0;
  const summary = tableSummary({ start, end: current * pageSize + visible.length, loaded: sorted.length, total });

  function toggleSort(key: string) {
    setSort((value) => (value?.key === key ? { key, dir: value.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
    setPage(0);
  }

  async function copyCsv() {
    try {
      await copyText(rowsToCsv(keys, sorted));
      toast.success("CSV copiado al portapapeles.");
    } catch {
      toast.error("No se pudo copiar el CSV.");
    }
  }

  function exportCsv() {
    downloadText(csvFilename(exportName ?? "vista-previa"), rowsToCsv(keys, sorted));
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p aria-live="polite" data-testid="table-summary" className="text-xs text-muted-foreground">
          {summary}
        </p>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={buttonClass} onClick={copyCsv} disabled={!sorted.length}>
            <Copy aria-hidden className="h-4 w-4" /> Copiar CSV
          </button>
          <button type="button" className={buttonClass} onClick={exportCsv} disabled={!sorted.length}>
            <Download aria-hidden className="h-4 w-4" /> Exportar CSV
          </button>
        </div>
      </div>
      <div className={cn("overflow-auto rounded-md border", maxHeight)}>
        <table className="min-w-full divide-y text-xs">
          {caption ? <caption className="sr-only">{caption}</caption> : null}
          <thead className="sticky top-0 bg-muted text-left text-muted-foreground">
            <tr>
              {keys.map((key) => {
                const active = sort?.key === key ? sort : null;
                const Icon = active ? (active.dir === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;
                return (
                  <th
                    key={key}
                    scope="col"
                    aria-sort={active ? SORT_LABEL[active.dir] : "none"}
                    className="whitespace-nowrap px-1 py-1 font-medium"
                  >
                    <button
                      type="button"
                      title={`Ordenar por ${key}`}
                      onClick={() => toggleSort(key)}
                      className={cn(
                        "inline-flex min-h-[32px] items-center gap-1 rounded px-2 uppercase",
                        "hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        active && "text-foreground",
                      )}
                    >
                      {key}
                      <Icon aria-hidden className="h-3 w-3" />
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="divide-y font-mono">
            {visible.map((row, index) => (
              <tr key={`${current}-${index}`} className="hover:bg-muted/40">
                {keys.map((key) => {
                  const text = formatCell(row[key]);
                  return (
                    <td key={key} title={text} className="max-w-[260px] truncate whitespace-nowrap px-3 py-1.5">
                      {text}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 ? (
        <nav aria-label="Paginación de la tabla" className="flex flex-wrap items-center justify-end gap-2 text-xs">
          <button
            type="button"
            className={buttonClass}
            onClick={() => setPage(current - 1)}
            disabled={current === 0}
          >
            <ChevronLeft aria-hidden className="h-4 w-4" /> Anterior
          </button>
          <span className="text-muted-foreground">
            Página {current + 1} de {pages}
          </span>
          <button
            type="button"
            className={buttonClass}
            onClick={() => setPage(current + 1)}
            disabled={current >= pages - 1}
          >
            Siguiente <ChevronRight aria-hidden className="h-4 w-4" />
          </button>
        </nav>
      ) : null}
    </div>
  );
}
