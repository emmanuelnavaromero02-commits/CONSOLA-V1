"use client";

import { type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Coins, MessageSquareText, RefreshCw, Timer } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { WorkspaceTokenKeys } from "@/components/copilot/WorkspaceTokenKeys";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface TokenModelSummary {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  calls: number;
  cost_usd: number | null;
  priced?: boolean;
}

interface TokenSummary {
  available?: boolean;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_creation_tokens: number | null;
  cache_read_tokens: number | null;
  calls: number | null;
  cost_usd: number | null;
  models: TokenModelSummary[];
  unpriced_models?: string[];
  avg_response_ms?: number | null;
  queries_count?: number | null;
}

const NO_DATA = "Sin información";

export default function CopilotTokensPage() {
  const summary = useQuery({
    queryKey: ["copilot", "tokens", "summary"],
    queryFn: async () => {
      const { data } = await api.get<TokenSummary>("/tokens/summary");
      return data;
    },
  });

  const available = summary.data ? summary.data.available !== false : false;
  const hasData = Boolean(summary.data) && available;
  const data = summary.data;
  const unpricedModels = (data?.unpriced_models ?? []).filter(Boolean);
  const totalTokens = Number(data?.input_tokens ?? 0)
    + Number(data?.output_tokens ?? 0)
    + Number(data?.cache_creation_tokens ?? 0)
    + Number(data?.cache_read_tokens ?? 0);

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-6">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Inversión y Uso del Asistente</h1>
          <p className="text-sm text-muted-foreground">
            Cuánto se usa el asistente, cómo responde y cuánto ha costado.
          </p>
        </div>
        <button
          type="button"
          onClick={() => summary.refetch()}
          className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RefreshCw aria-hidden className={cn("h-4 w-4", summary.isFetching && "animate-spin")} />
          Refrescar
        </button>
      </header>

      {summary.isError ? (
        <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <span>No se pudo cargar la información de uso.</span>
            <button
              type="button"
              onClick={() => summary.refetch()}
              className="inline-flex min-h-[40px] items-center justify-center rounded-md border bg-background px-3 text-sm font-medium text-foreground hover:bg-accent/5"
            >
              Reintentar
            </button>
          </div>
        </div>
      ) : null}

      {data && !available ? (
        <div role="alert" className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">
          La información de uso no está disponible en este momento.
        </div>
      ) : null}

      <section className="grid grid-cols-1 gap-4 md:grid-cols-3" aria-label="Resumen de uso del asistente">
        <MetricCard
          icon={MessageSquareText}
          label="Consultas realizadas"
          value={hasData && data?.queries_count != null ? formatInteger(data.queries_count) : summary.isError || !summary.data ? "—" : NO_DATA}
          loading={summary.isLoading}
        />
        <MetricCard
          icon={Timer}
          label="Tiempo de respuesta promedio"
          value={hasData && data?.avg_response_ms != null ? formatDuration(data.avg_response_ms) : summary.isError || !summary.data ? "—" : NO_DATA}
          loading={summary.isLoading}
        />
        <MetricCard
          icon={Coins}
          label="Inversión acumulada (USD)"
          value={hasData && data?.cost_usd != null ? formatCurrency(data.cost_usd) : summary.isError || !summary.data ? "—" : NO_DATA}
          hint={hasData && unpricedModels.length ? "No incluye modelos sin precio configurado" : undefined}
          loading={summary.isLoading}
        />
      </section>

      {hasData && unpricedModels.length ? (
        <section aria-label="Modelos sin precio configurado" className="rounded-md border bg-muted/20 p-4 text-sm">
          <p className="text-muted-foreground">
            La inversión acumulada no incluye modelos sin precio configurado:
          </p>
          <ul className="mt-2 flex flex-wrap gap-2">
            {unpricedModels.map((model) => (
              <li key={model} className="inline-flex items-center gap-2 rounded-full border bg-background px-3 py-1 text-xs">
                <span className="font-medium">{model}</span>
                <span className="rounded-full border border-amber-400/60 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-400">
                  Sin precio configurado
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <details className="rounded-lg border bg-card shadow-sm">
        <summary className="cursor-pointer select-none px-4 py-3 text-base font-semibold">
          Avanzado
        </summary>
        <div className="space-y-4 border-t p-4">
          <WorkspaceTokenKeys />

          <section className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4" aria-label="Detalle técnico de uso">
            <MetricCard label="Llamadas al proveedor" value={hasData ? formatInteger(data?.calls ?? 0) : "—"} loading={summary.isLoading} />
            <MetricCard label="Tokens totales" value={hasData ? formatInteger(totalTokens) : "—"} loading={summary.isLoading} />
            <MetricCard label="Entrada" value={hasData ? formatInteger(data?.input_tokens ?? 0) : "—"} loading={summary.isLoading} />
            <MetricCard label="Caché" value={hasData ? formatInteger(Number(data?.cache_creation_tokens ?? 0) + Number(data?.cache_read_tokens ?? 0)) : "—"} loading={summary.isLoading} />
          </section>

          <section className="rounded-lg border bg-card shadow-sm">
            <header className="flex flex-col gap-1 border-b px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-base font-semibold">Modelos</h2>
                <p className="text-xs text-muted-foreground">Distribución de uso registrada por proveedor/modelo.</p>
              </div>
              <span className="rounded-md border bg-background px-2 py-1 text-xs text-muted-foreground">
                {summary.isLoading ? "... modelos" : hasData ? `${data?.models?.length ?? 0} modelos` : "— modelos"}
              </span>
            </header>

            {summary.isLoading ? (
              <SkeletonRows rows={5} />
            ) : !hasData ? (
              <EmptyState label="No hay datos disponibles." />
            ) : (
              <ModelsTable rows={data?.models ?? []} />
            )}
          </section>
        </div>
      </details>
    </main>
  );
}

function ModelsTable({ rows }: { rows: TokenModelSummary[] }) {
  if (rows.length === 0) return <EmptyState label="Todavía no hay consumo registrado por modelo." />;

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y text-sm">
        <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-4 py-3 text-left font-medium">Modelo</th>
            <th className="px-4 py-3 text-right font-medium">Llamadas</th>
            <th className="px-4 py-3 text-right font-medium">Entrada</th>
            <th className="px-4 py-3 text-right font-medium">Salida</th>
            <th className="px-4 py-3 text-right font-medium">Caché</th>
            <th className="px-4 py-3 text-right font-medium">Costo</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((row) => (
            <tr key={row.model}>
              <td className="max-w-[280px] truncate px-4 py-3 font-medium">
                {row.model || "sin modelo"}
                {row.priced === false ? (
                  <span className="ml-2 rounded-full border border-amber-400/60 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-400">
                    Sin precio configurado
                  </span>
                ) : null}
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">{formatInteger(row.calls)}</td>
              <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">{formatInteger(row.input_tokens)}</td>
              <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">{formatInteger(row.output_tokens)}</td>
              <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                {formatInteger(Number(row.cache_creation_tokens ?? 0) + Number(row.cache_read_tokens ?? 0))}
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                {row.cost_usd != null ? formatCurrency(row.cost_usd) : NO_DATA}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  hint,
  loading,
}: {
  icon?: LucideIcon;
  label: string;
  value: ReactNode;
  hint?: string;
  loading?: boolean;
}) {
  return (
    <div className="rounded-lg border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold">{loading ? "..." : value}</p>
          {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
        </div>
        {Icon ? (
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-primary">
            <Icon aria-hidden className="h-5 w-5" />
          </span>
        ) : null}
      </div>
    </div>
  );
}

function SkeletonRows({ rows }: { rows: number }) {
  return (
    <div role="status" aria-busy="true" aria-label="Cargando información de uso" className="divide-y">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="grid grid-cols-6 gap-4 px-4 py-4">
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return <div className="px-4 py-10 text-center text-sm text-muted-foreground">{label}</div>;
}

function formatInteger(value: number): string {
  return new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0 }).format(Number(value ?? 0));
}

function formatDuration(valueMs: number): string {
  const ms = Number(valueMs ?? 0);
  if (ms >= 1000) {
    return `${new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1 }).format(ms / 1000)} s`;
  }
  return `${new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0 }).format(ms)} ms`;
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 4,
  }).format(Number(value ?? 0));
}
