"use client";

import { useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { DatabaseZap, Loader2, Play, RotateCcw, Table2 } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { toast } from "sonner";

import { AssistedExplorer } from "@/components/explorer/AssistedExplorer";
import { TechnicalSqlDisclosure } from "@/components/explorer/TechnicalSqlDisclosure";
import { CodeEditor } from "@/components/studio/CodeEditor";
import { DataTable } from "@/components/studio/DataTable";
import { Notice } from "@/components/studio/ui";
import { queryBronze } from "@/lib/data/client";
import type { BronzeQueryPayload, BronzeRow } from "@/lib/data/types";
import { describeSource, exploreData, exploreRowsAsRecords } from "@/lib/explorer/client";
import {
  BRONZE_ROW_CAP,
  EMPTY_SPEC,
  buildExploreRequest,
  clampRows,
  rowCapFor,
  sourceFromKey,
  specProblems,
  type ExplorerSpec,
} from "@/lib/explorer/spec";
import { useSources } from "@/lib/monitor/hooks";

type RunKind = "builder" | "technical";

export default function BronzeQueryPage() {
  const sources = useSources();
  const [selectedKey, setSelectedKey] = useState("");
  const [spec, setSpec] = useState<ExplorerSpec>(EMPTY_SPEC);
  const [technicalOpen, setTechnicalOpen] = useState(false);
  const [sql, setSql] = useState("");
  const [lastRun, setLastRun] = useState<RunKind | null>(null);

  const options = useMemo(() => sourceOptions(sources.data ?? []), [sources.data]);
  const source = sourceFromKey(selectedKey);
  const rowCap = rowCapFor(source);

  const schema = useQuery({
    queryKey: ["explorer", "schema", selectedKey],
    queryFn: () => describeSource(source!),
    enabled: Boolean(source),
    retry: false,
    staleTime: 60_000,
  });
  const columns = schema.data?.available_columns ?? [];
  const latestAvailable = source?.kind === "bronze" && columns.some((column) => column.name === "load_date");

  const explore = useMutation({
    mutationFn: () => exploreData(buildExploreRequest(source!, spec, { execute: true })),
    onSuccess: (payload) => toast.success(`Consulta lista: ${payload.row_count.toLocaleString("es-MX")} filas.`),
    onError: (error) => toast.error(errorMessage(error)),
  });
  const technical = useMutation({
    mutationFn: () =>
      queryBronze({
        sql: sql.trim(),
        limit: clampRows(spec.limit, BRONZE_ROW_CAP),
        sources: technicalSources(sql, selectedKey),
      }),
    onSuccess: (payload) => {
      if (payload.error) {
        toast.error(String(payload.error));
        return;
      }
      toast.success("Consulta ejecutada.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const running = explore.isPending || technical.isPending;
  const problems = source ? specProblems(spec, columns) : [];
  const result = lastRun === "builder" ? builderResult(explore) : lastRun === "technical" ? technicalResult(technical) : null;
  const status = running ? "Ejecutando" : result?.error ? "Con error" : result ? "Lista" : "Sin ejecutar";

  function chooseSource(key: string) {
    setSelectedKey(key);
    setSpec((current) => ({ ...EMPTY_SPEC, limit: clampRows(current.limit, rowCapFor(sourceFromKey(key))) }));
    setLastRun(null);
    explore.reset();
  }

  function runBuilder() {
    if (!source) {
      toast.error("Elige una fuente de datos.");
      return;
    }
    if (problems.length) {
      toast.error(problems[0]);
      return;
    }
    setLastRun("builder");
    explore.mutate();
  }

  function runTechnical() {
    if (!sql.trim()) {
      toast.error("Escribe una consulta SQL.");
      return;
    }
    setLastRun("technical");
    technical.mutate();
  }

  function reset() {
    setSelectedKey("");
    setSpec(EMPTY_SPEC);
    setSql("");
    setTechnicalOpen(false);
    setLastRun(null);
    explore.reset();
    technical.reset();
  }

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-6">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Consulta Bronze</h1>
          <p className="text-sm text-muted-foreground">
            Explora la capa cruda con filtros guiados. La ejecución está protegida por el backend y la consulta SQL técnica
            queda disponible para usuarios avanzados.
          </p>
        </div>
        <button
          type="button"
          onClick={reset}
          className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RotateCcw aria-hidden className="h-4 w-4" />
          Reiniciar
        </button>
      </header>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-3" aria-label="Resumen de la consulta">
        <MetricCard icon={DatabaseZap} label="Filas a mostrar" value={clampRows(spec.limit, rowCap).toLocaleString("es-MX")} />
        <MetricCard icon={Table2} label="Filas" value={(result?.rows.length ?? 0).toLocaleString("es-MX")} />
        <MetricCard icon={Play} label="Estado" value={status} />
      </section>

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
        <div className="space-y-4 rounded-lg border bg-card p-4 shadow-sm">
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">Fuente de datos</span>
            <select
              value={selectedKey}
              onChange={(event) => chooseSource(event.target.value)}
              className="min-h-[44px] w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value="">{sources.isLoading ? "Cargando fuentes…" : "Elige una fuente de datos"}</option>
              {options.bronze.length ? (
                <optgroup label="Bronze (datos crudos)">
                  {options.bronze.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
                </optgroup>
              ) : null}
              {options.gold.length ? (
                <optgroup label="Oro (datasets publicados)">
                  {options.gold.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
                </optgroup>
              ) : null}
            </select>
          </label>
          {sources.isError ? <Notice tone="error">{errorMessage(sources.error)}</Notice> : null}
          {!sources.isLoading && !sources.isError && !options.bronze.length && !options.gold.length ? (
            <Notice>No hay fuentes de datos visibles en este espacio de trabajo.</Notice>
          ) : null}

          {source && !technicalOpen ? (
            <>
              <AssistedExplorer
                columns={columns}
                spec={spec}
                onSpecChange={setSpec}
                rowCap={rowCap}
                latestAvailable={latestAvailable}
                loading={schema.isLoading}
                error={schema.isError ? errorMessage(schema.error) : null}
                disabled={running}
              />
              <button
                type="button"
                onClick={runBuilder}
                disabled={running || schema.isLoading || !columns.length}
                className="inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {explore.isPending ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Play aria-hidden className="h-4 w-4" />}
                Consultar
              </button>
            </>
          ) : null}
          {!source && !technicalOpen ? (
            <Notice>Elige una fuente de datos para construir la consulta sin escribir SQL.</Notice>
          ) : null}

          <TechnicalSqlDisclosure
            open={technicalOpen}
            onToggle={setTechnicalOpen}
            generatedSql={explore.data?.sql_display ?? null}
            onUseGenerated={(generated) => setSql(generated)}
          >
            <div className="flex flex-col gap-1 text-sm">
              <label htmlFor="bronze-sql" className="font-medium">SQL</label>
              <CodeEditor
                id="bronze-sql"
                name="sql"
                value={sql}
                onChange={setSql}
                rows={12}
                placeholder={`select * from read_parquet('${selectedKey.startsWith("raw/") ? selectedKey : "raw/<fuente>/<entidad>"}') limit 50`}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              Máximo {BRONZE_ROW_CAP.toLocaleString("es-MX")} filas; solo lectura sobre las fuentes de tu espacio de trabajo.
            </p>
            <button
              type="button"
              onClick={runTechnical}
              disabled={running}
              className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {technical.isPending ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Play aria-hidden className="h-4 w-4" />}
              Ejecutar SQL
            </button>
          </TechnicalSqlDisclosure>
        </div>

        <div className="rounded-lg border bg-card shadow-sm">
          <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
            <h2 className="text-base font-semibold">Resultado</h2>
            <span className="text-xs text-muted-foreground">
              {(result?.rows.length ?? 0).toLocaleString("es-MX")} filas
            </span>
          </header>
          <div className="space-y-3 p-4">
            {running ? (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> Ejecutando consulta…
              </p>
            ) : result?.error ? (
              <Notice tone="error">{result.error}</Notice>
            ) : !result ? (
              <p className="py-6 text-center text-sm text-muted-foreground">Ejecuta una consulta para ver resultados.</p>
            ) : !result.rows.length ? (
              <p className="py-6 text-center text-sm text-muted-foreground">La consulta no devolvió filas.</p>
            ) : (
              <>
                {result.truncated ? (
                  <Notice>Se muestran las primeras {result.rows.length.toLocaleString("es-MX")} filas (límite alcanzado).</Notice>
                ) : null}
                <DataTable
                  columns={result.columns}
                  rows={result.rows}
                  caption="Resultado de la consulta"
                  maxHeight="max-h-[640px]"
                  exportName={selectedKey ? selectedKey.replace(/\//g, "_") : "consulta-bronze"}
                />
              </>
            )}
          </div>
        </div>
      </section>
    </main>
  );
}

interface RunResult {
  columns: string[];
  rows: Array<Record<string, unknown>>;
  truncated: boolean;
  error: string | null;
}

function builderResult(explore: {
  data?: Awaited<ReturnType<typeof exploreData>>;
  isError: boolean;
  error: unknown;
}): RunResult | null {
  if (explore.isError) return { columns: [], rows: [], truncated: false, error: errorMessage(explore.error) };
  if (!explore.data) return null;
  return { columns: explore.data.columns, rows: exploreRowsAsRecords(explore.data), truncated: explore.data.truncated, error: null };
}

function technicalResult(technical: { data?: BronzeQueryPayload; isError: boolean; error: unknown }): RunResult | null {
  if (technical.isError) return { columns: [], rows: [], truncated: false, error: errorMessage(technical.error) };
  if (!technical.data) return null;
  if (technical.data.error) return { columns: [], rows: [], truncated: false, error: String(technical.data.error) };
  const rows = normalizeRows(technical.data);
  return { columns: normalizeColumns(technical.data, rows), rows, truncated: false, error: null };
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

interface SourceOption {
  key: string;
  label: string;
}

function sourceOptions(sources: string[]): { bronze: SourceOption[]; gold: SourceOption[] } {
  const bronze: SourceOption[] = [];
  const gold: SourceOption[] = [];
  for (const key of [...new Set(sources)].sort()) {
    const source = sourceFromKey(key);
    if (!source) continue;
    if (source.kind === "bronze") bronze.push({ key, label: `${source.cartridge} · ${source.entity}` });
    else gold.push({ key, label: source.name });
  }
  return { bronze, gold };
}

function normalizeRows(payload: BronzeQueryPayload | null): BronzeRow[] {
  const candidate = payload?.rows ?? payload?.data ?? payload?.result;
  return Array.isArray(candidate) ? candidate.filter(isRecord) : [];
}

function normalizeColumns(payload: BronzeQueryPayload | null, rows: BronzeRow[]): string[] {
  if (Array.isArray(payload?.columns) && payload.columns.length > 0) return payload.columns;
  const schema = (payload as { schema?: unknown } | null)?.schema;
  if (Array.isArray(schema) && schema.length) {
    return schema
      .map((item) => (item && typeof item === "object" ? String((item as { name?: unknown }).name ?? "") : String(item)))
      .filter(Boolean);
  }
  const keys = new Set<string>();
  for (const row of rows.slice(0, 25)) {
    for (const key of Object.keys(row)) keys.add(key);
  }
  return [...keys];
}

function isRecord(value: unknown): value is BronzeRow {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : "No se pudo ejecutar la consulta.";
}

function technicalSources(sql: string, selectedKey: string): string[] {
  const matches = Array.from(
    sql.matchAll(/\bread_parquet\s*\(\s*['"]([^'"]+)['"]/gi),
    (match) => match[1]?.trim(),
  ).filter(Boolean) as string[];
  if (!matches.length && selectedKey.startsWith("raw/")) matches.push(selectedKey);
  return Array.from(new Set(matches));
}
