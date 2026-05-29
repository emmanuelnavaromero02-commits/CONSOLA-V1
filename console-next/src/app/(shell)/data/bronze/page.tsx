"use client";

import { useMemo, useState, type ReactNode } from "react";
import { useMutation } from "@tanstack/react-query";
import { DatabaseZap, Loader2, Play, RotateCcw, Table2 } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { toast } from "sonner";

import { queryBronze } from "@/lib/data/client";
import type { BronzeQueryPayload, BronzeRow } from "@/lib/data/types";
import { cn } from "@/lib/utils";

const DEFAULT_SQL = "select * from bronze limit 50";

export default function BronzeQueryPage() {
  const [sql, setSql] = useState(DEFAULT_SQL);
  const [limit, setLimit] = useState(200);
  const [sources, setSources] = useState("");
  const [result, setResult] = useState<BronzeQueryPayload | null>(null);

  const bronzeQuery = useMutation({
    mutationFn: () => queryBronze({
      sql: sql.trim(),
      limit,
      sources: splitCsv(sources),
    }),
    onMutate: () => {
      setResult(null);
    },
    onSuccess: (payload) => {
      setResult(payload);
      if (payload.error) {
        toast.error(payload.error);
        return;
      }
      toast.success("Consulta ejecutada.");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo ejecutar la consulta."),
  });

  const rows = useMemo(() => normalizeRows(result), [result]);
  const columns = useMemo(() => normalizeColumns(result, rows), [result, rows]);
  const inlineError = result?.error || (bronzeQuery.isError ? toErrorMessage(bronzeQuery.error) : "");
  const queryStatus = bronzeQuery.isPending ? "running" : inlineError ? "error" : result ? "ready" : "idle";

  function submitQuery() {
    if (!sql.trim()) {
      toast.error("SQL requerido.");
      return;
    }
    bronzeQuery.mutate();
  }

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-6">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Bronze Query</h1>
          <p className="text-sm text-muted-foreground">
            Consulta directa a capa cruda con ejecución protegida por el backend.
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            setSql(DEFAULT_SQL);
            setSources("");
            setLimit(200);
            setResult(null);
          }}
          className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RotateCcw aria-hidden className="h-4 w-4" />
          Reiniciar
        </button>
      </header>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-3" aria-label="Resumen de consulta bronze">
        <MetricCard icon={DatabaseZap} label="Límite" value={limit} />
        <MetricCard icon={Table2} label="Filas" value={rows.length} />
        <MetricCard icon={Play} label="Estado" value={queryStatus} />
      </section>

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div className="rounded-lg border bg-card p-4 shadow-sm">
          <div className="space-y-4">
            <label className="block space-y-1 text-sm">
              <span className="text-xs font-medium uppercase text-muted-foreground">SQL</span>
              <textarea
                value={sql}
                onChange={(event) => setSql(event.target.value)}
                rows={13}
                spellCheck={false}
                className="w-full rounded-md border bg-background px-3 py-3 font-mono text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-[160px_minmax(0,1fr)]">
              <label className="space-y-1 text-sm">
                <span className="text-xs font-medium uppercase text-muted-foreground">Limit</span>
                <input
                  type="number"
                  min={1}
                  max={2000}
                  value={limit}
                  onChange={(event) => setLimit(clamp(Number(event.target.value) || 1, 1, 2000))}
                  className="min-h-[44px] w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </label>
              <label className="space-y-1 text-sm">
                <span className="text-xs font-medium uppercase text-muted-foreground">Sources</span>
                <input
                  value={sources}
                  onChange={(event) => setSources(event.target.value)}
                  placeholder="raw/replicon/Client, raw/replicon/Project"
                  className="min-h-[44px] w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </label>
            </div>
            <button
              type="button"
              onClick={submitQuery}
              disabled={bronzeQuery.isPending}
              className="inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {bronzeQuery.isPending ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Play aria-hidden className="h-4 w-4" />}
              Ejecutar
            </button>
          </div>
        </div>

        <div className="rounded-lg border bg-card shadow-sm">
          <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
            <h2 className="text-base font-semibold">Resultado</h2>
            <span className="text-xs text-muted-foreground">{rows.length} filas</span>
          </header>
          {bronzeQuery.isPending ? (
            <SkeletonRows rows={6} />
          ) : inlineError ? (
            <div className="m-4 rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">{inlineError}</div>
          ) : rows.length === 0 ? (
            <EmptyState label={result ? "Sin filas." : "Ejecuta una consulta para ver resultados."} />
          ) : (
            <ResultTable rows={rows} columns={columns} />
          )}
        </div>
      </section>
    </main>
  );
}

function ResultTable({ rows, columns }: { rows: BronzeRow[]; columns: string[] }) {
  return (
    <div className="max-h-[680px] overflow-auto">
      <table className="min-w-full divide-y text-sm">
        <thead className="sticky top-0 bg-muted text-xs uppercase text-muted-foreground">
          <tr>
            {columns.map((column) => (
              <th key={column} className="px-4 py-3 text-left font-medium">{column}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={`${index}:${column}`} className="max-w-sm truncate px-4 py-3 text-muted-foreground">
                  {formatCell(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MetricCard({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: ReactNode }) {
  return (
    <div className="rounded-lg border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold">{value}</p>
        </div>
        <span className="inline-flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Icon aria-hidden className="h-5 w-5" />
        </span>
      </div>
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return <div className="px-4 py-10 text-center text-sm text-muted-foreground">{label}</div>;
}

function SkeletonRows({ rows }: { rows: number }) {
  return (
    <div className="divide-y">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="grid grid-cols-4 gap-4 px-4 py-4">
          <div className={cn("h-4 rounded bg-muted", index % 2 === 0 && "col-span-2")} />
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

function normalizeRows(payload: BronzeQueryPayload | null): BronzeRow[] {
  const candidate = payload?.rows ?? payload?.data ?? payload?.result;
  return Array.isArray(candidate) ? candidate.filter(isRecord) : [];
}

function normalizeColumns(payload: BronzeQueryPayload | null, rows: BronzeRow[]): string[] {
  if (Array.isArray(payload?.columns) && payload.columns.length > 0) return payload.columns;
  const keys = new Set<string>();
  for (const row of rows.slice(0, 25)) {
    for (const key of Object.keys(row)) keys.add(key);
  }
  return [...keys];
}

function isRecord(value: unknown): value is BronzeRow {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function toErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "No se pudo ejecutar la consulta.";
}

function splitCsv(value: string): string[] {
  return value.split(",").map((part) => part.trim()).filter(Boolean);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
