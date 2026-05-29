"use client";

import { type ReactNode } from "react";
import { Activity, AlertTriangle, Gauge, RefreshCw, Server, Timer, Workflow } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { useOperationalMetrics, useOperationsHealth } from "@/lib/operations/hooks";
import type { ServiceProbe, SlowEntityMetric } from "@/lib/operations/types";
import { cn } from "@/lib/utils";

export default function OperationsMetricsPage() {
  const metrics = useOperationalMetrics();
  const health = useOperationsHealth();
  const metricsUnavailable = metrics.isLoading || (metrics.isError && !metrics.data);
  const healthUnavailable = health.isLoading || (health.isError && !health.data);
  const failureRate = !metricsUnavailable && metrics.data && metrics.data.extractions_24h > 0
    ? (metrics.data.errors_24h / metrics.data.extractions_24h) * 100
    : 0;

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-6">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Métricas operacionales</h1>
          <p className="text-sm text-muted-foreground">
            Salud de servicios, actividad de extracción y carga operativa reciente.
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            metrics.refetch();
            health.refetch();
          }}
          className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RefreshCw aria-hidden className={cn("h-4 w-4", (metrics.isFetching || health.isFetching) && "animate-spin")} />
          Refrescar
        </button>
      </header>

      {metrics.isError ? <ErrorPanel message="No se pudieron cargar métricas operacionales." onRetry={() => metrics.refetch()} /> : null}
      {health.isError ? <ErrorPanel message="No se pudo cargar la salud de servicios." onRetry={() => health.refetch()} /> : null}

      <section className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4" aria-label="Resumen operacional">
        <MetricCard icon={Workflow} label="Extracciones 24h" value={metrics.data?.extractions_24h ?? 0} loading={metricsUnavailable} />
        <MetricCard icon={AlertTriangle} label="Errores 24h" value={metrics.data?.errors_24h ?? 0} loading={metricsUnavailable} />
        <MetricCard icon={Timer} label="Duración media" value={`${formatSeconds(metrics.data?.avg_duration_seconds ?? 0)}`} loading={metricsUnavailable} />
        <MetricCard icon={Gauge} label="Fallo" value={`${failureRate.toFixed(1)}%`} loading={metricsUnavailable} />
      </section>

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(360px,0.75fr)]">
        <div className="rounded-lg border bg-card shadow-sm">
          <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
            <div>
              <h2 className="text-base font-semibold">Servicios</h2>
              <p className="text-xs text-muted-foreground">Versión {health.data?.version ?? "unknown"}</p>
            </div>
            <span className="rounded-md border bg-background px-2 py-1 text-xs text-muted-foreground">
              {healthUnavailable ? ".../... up" : `${health.data?.summary.up ?? 0}/${health.data?.summary.total ?? 0} up`}
            </span>
          </header>
          {health.isLoading ? (
            <SkeletonRows rows={7} />
          ) : health.isError && !health.data ? (
            <ErrorPanel message="No se pudo cargar la salud de servicios." onRetry={() => health.refetch()} compact />
          ) : (
            <ServicesTable services={health.data?.services ?? []} />
          )}
        </div>

        <div className="space-y-4">
          <div className="rounded-lg border bg-card shadow-sm">
            <header className="border-b px-4 py-3">
              <h2 className="text-base font-semibold">Más lentas 7d</h2>
            </header>
            {metrics.isLoading ? (
              <SkeletonRows rows={5} />
            ) : metrics.isError && !metrics.data ? (
              <ErrorPanel message="No se pudieron cargar entidades lentas." onRetry={() => metrics.refetch()} compact />
            ) : (
              <SlowEntities rows={metrics.data?.slowest_entities_7d ?? []} />
            )}
          </div>

          <div className="rounded-lg border bg-card p-4 shadow-sm">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-xs font-medium uppercase text-muted-foreground">Auditoría 24h</p>
                <p className="mt-1 text-2xl font-semibold">{metricsUnavailable ? "..." : (metrics.data?.audit_events_24h ?? 0)}</p>
              </div>
              <span className="inline-flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-primary">
                <Activity aria-hidden className="h-5 w-5" />
              </span>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}

function ServicesTable({ services }: { services: ServiceProbe[] }) {
  if (services.length === 0) return <EmptyState label="Sin probes de servicio." />;

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y text-sm">
        <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-4 py-3 text-left font-medium">Servicio</th>
            <th className="px-4 py-3 text-left font-medium">Estado</th>
            <th className="px-4 py-3 text-left font-medium">Código</th>
            <th className="px-4 py-3 text-left font-medium">Error</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {services.map((service) => (
            <tr key={service.name}>
              <td className="px-4 py-3 font-medium">{service.name}</td>
              <td className="px-4 py-3"><ServiceStatus status={service.status} /></td>
              <td className="px-4 py-3 text-muted-foreground">{service.code ?? "n/a"}</td>
              <td className="px-4 py-3 text-muted-foreground">{service.error ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SlowEntities({ rows }: { rows: SlowEntityMetric[] }) {
  if (rows.length === 0) return <EmptyState label="Sin entidades lentas." />;

  return (
    <div className="divide-y">
      {rows.map((row, index) => (
        <div key={`${row.cartridge_id}:${row.entity_name}:${index}`} className="flex items-center justify-between gap-3 px-4 py-3 text-sm">
          <div className="min-w-0">
            <div className="truncate font-medium">{row.entity_name || "Entidad"}</div>
            <div className="truncate text-xs text-muted-foreground">{row.cartridge_id || "sin cartucho"}</div>
          </div>
          <span className="rounded-md border bg-background px-2 py-1 text-xs font-medium text-muted-foreground">
            {formatSeconds(Number(row.avg_sec ?? 0))}
          </span>
        </div>
      ))}
    </div>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  loading,
}: {
  icon: LucideIcon;
  label: string;
  value: ReactNode;
  loading?: boolean;
}) {
  return (
    <div className="rounded-lg border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold">{loading ? "..." : value}</p>
        </div>
        <span className="inline-flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Icon aria-hidden className="h-5 w-5" />
        </span>
      </div>
    </div>
  );
}

function ServiceStatus({ status }: { status: string }) {
  const up = status === "up";
  return (
    <span className={cn(
      "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium",
      up
        ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
        : "border-destructive/30 bg-destructive/10 text-destructive",
    )}>
      <Server aria-hidden className="h-3.5 w-3.5" />
      {status}
    </span>
  );
}

function ErrorPanel({
  message,
  onRetry,
  compact,
}: {
  message: string;
  onRetry: () => void;
  compact?: boolean;
}) {
  return (
    <div className={cn("rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive", compact && "m-4")}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <span>{message}</span>
        <button type="button" onClick={onRetry} className="inline-flex min-h-[40px] items-center justify-center rounded-md border bg-background px-3 text-sm font-medium text-foreground hover:bg-accent/5">
          Reintentar
        </button>
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
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

function formatSeconds(value: number): string {
  if (!Number.isFinite(value)) return "0s";
  if (value < 60) return `${value.toFixed(1)}s`;
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value % 60);
  return `${minutes}m ${seconds}s`;
}
