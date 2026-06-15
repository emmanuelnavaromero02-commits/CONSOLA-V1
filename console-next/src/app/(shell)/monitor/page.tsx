"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Activity, CircleCheck, CircleX, Clock3, RefreshCcw } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { JobTable } from "@/components/monitor/JobTable";
import { useJobs } from "@/lib/monitor/hooks";

export default function MonitorPage() {
  const [limit, setLimit] = useState(50);
  const jobs = useJobs(100);

  const visibleJobs = useMemo(() => (jobs.data ?? []).slice(0, limit), [jobs.data, limit]);
  const running = useMemo(
    () => (jobs.data ?? []).filter((job) => ["queued", "running"].includes(String(job.status).toLowerCase())).length,
    [jobs.data],
  );
  const failed = useMemo(
    () => (jobs.data ?? []).filter((job) => ["failed", "error"].includes(String(job.status).toLowerCase())).length,
    [jobs.data],
  );
  const completed = useMemo(
    () => (jobs.data ?? []).filter((job) => ["done", "success"].includes(String(job.status).toLowerCase())).length,
    [jobs.data],
  );

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-8">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-3xl font-semibold tracking-tight">Ejecuciones y logs</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">
            Historial operativo de extracción. Las vistas técnicas viven en Catálogo técnico.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={limit}
            onChange={(event) => setLimit(Number(event.target.value) || 50)}
            className="min-h-[44px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Límite de jobs"
          >
            {[25, 50, 100].map((value) => <option key={value} value={value}>{value} jobs</option>)}
          </select>
          <button
            type="button"
            onClick={() => jobs.refetch()}
            className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <RefreshCcw aria-hidden className="h-4 w-4" />
            Refrescar
          </button>
        </div>
      </header>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-4" aria-label="Resumen de ejecuciones">
        <MetricCard icon={Activity} label="Activos" value={running} tone={running > 0 ? "warning" : "success"} />
        <MetricCard icon={CircleX} label="Fallidos" value={failed} tone={failed > 0 ? "warning" : "success"} />
        <MetricCard icon={CircleCheck} label="Completados" value={completed} />
        <MetricCard icon={Clock3} label="Mostrando" value={visibleJobs.length} />
      </section>

      <section className="space-y-3" aria-label="Ejecuciones recientes">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-base font-semibold">Ejecuciones recientes</h2>
            <p className="text-xs text-muted-foreground">Historial de ejecución y deeplinks a logs.</p>
          </div>
          <Link
            href="/viewer?type=jobs"
            className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Abrir historial
          </Link>
        </div>
        {jobs.isError ? (
          <ErrorPanel message="No se pudieron cargar jobs." onRetry={() => jobs.refetch()} />
        ) : jobs.isLoading ? (
          <SkeletonRows />
        ) : (
          <JobTable jobs={visibleJobs} />
        )}
      </section>
    </main>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  tone = "neutral",
}: {
  icon: LucideIcon;
  label: string;
  value: number;
  tone?: "neutral" | "success" | "warning";
}) {
  return (
    <article className="rounded-lg border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{label}</span>
        <Icon
          aria-hidden
          className={tone === "success" ? "h-4 w-4 text-success" : tone === "warning" ? "h-4 w-4 text-warning" : "h-4 w-4 text-muted-foreground"}
        />
      </div>
      <strong className="mt-2 block text-3xl font-semibold tracking-tight">{value}</strong>
    </article>
  );
}

function ErrorPanel({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
      <p className="font-medium text-destructive">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-2 inline-flex min-h-[44px] items-center justify-center rounded-md border border-destructive/40 px-3 text-xs font-medium text-destructive hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40"
      >
        Reintentar
      </button>
    </div>
  );
}

function SkeletonRows() {
  return (
    <div aria-busy="true" className="space-y-2">
      {Array.from({ length: 5 }).map((_, index) => (
        <span key={index} className="block h-12 animate-pulse rounded bg-muted" aria-hidden />
      ))}
    </div>
  );
}
