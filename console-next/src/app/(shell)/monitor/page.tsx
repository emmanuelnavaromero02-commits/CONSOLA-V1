"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Activity, Database, Droplets, Layers3, RefreshCcw } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { JobTable } from "@/components/monitor/JobTable";
import { PipelineTable } from "@/components/monitor/PipelineTable";
import { StatusPill } from "@/components/monitor/StatusPill";
import { useFreshness, useJobs, usePipeline } from "@/lib/monitor/hooks";

const CARTRIDGES = ["replicon", "hubspot", "sap_hcm", "sap_s4hana", "sap_successfactors"] as const;

export default function MonitorPage() {
  const [cartridge, setCartridge] = useState<string>("replicon");
  const jobs = useJobs(50);
  const pipeline = usePipeline(cartridge);
  const freshness = useFreshness(cartridge);

  const running = useMemo(
    () => (jobs.data ?? []).filter((job) => ["queued", "running"].includes(String(job.status).toLowerCase())).length,
    [jobs.data],
  );
  const stale = useMemo(
    () => (pipeline.data ?? []).filter((row) => row.bronze.status !== "fresh").length,
    [pipeline.data],
  );
  const watermarks = freshness.data ?? [];

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-8">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-3xl font-semibold tracking-tight">Monitor</h1>
          <p className="text-sm text-muted-foreground">
            Cómo van tus cargas de datos: qué se está procesando ahora y cuándo se actualizó cada fuente por última vez.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={cartridge}
            onChange={(event) => setCartridge(event.target.value)}
            className="min-h-[44px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Fuente de datos"
          >
            {CARTRIDGES.map((id) => <option key={id} value={id}>{id}</option>)}
          </select>
          <button
            type="button"
            onClick={() => {
              jobs.refetch();
              pipeline.refetch();
              freshness.refetch();
            }}
            className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <RefreshCcw aria-hidden className="h-4 w-4" />
            Refrescar
          </button>
        </div>
      </header>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-4" aria-label="Resumen operativo">
        <MetricCard icon={Activity} label="Tareas en curso" value={running} tone={running > 0 ? "warning" : "success"} />
        <MetricCard icon={Database} label="Tablas en proceso" value={pipeline.data?.length ?? 0} />
        <MetricCard icon={Droplets} label="Fuentes con datos" value={watermarks.length} />
        <MetricCard icon={Layers3} label="Datos atrasados" value={stale} tone={stale > 0 ? "warning" : "success"} />
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-[1.2fr_0.8fr]" aria-label="Vistas rápidas">
        <div className="space-y-3 rounded-lg border bg-card p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">Proceso de datos</h2>
              <p className="text-xs text-muted-foreground">Cada paso (sin procesar, limpio y listo) y cuándo corrió por última vez.</p>
            </div>
            <Link
              href={`/viewer?type=pipeline&cartridge=${encodeURIComponent(cartridge)}`}
              className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Ver detalle
            </Link>
          </div>
          {pipeline.isError ? (
            <ErrorPanel message="No se pudo cargar el proceso de datos." onRetry={() => pipeline.refetch()} />
          ) : pipeline.isLoading ? (
            <SkeletonRows />
          ) : (
            <PipelineTable
              rows={(pipeline.data ?? []).slice(0, 8)}
              cartridge={cartridge}
              onExtractionStarted={() => {
                jobs.refetch();
                pipeline.refetch();
                freshness.refetch();
              }}
            />
          )}
        </div>

        <div className="space-y-3 rounded-lg border bg-card p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">Última actualización</h2>
              <p className="text-xs text-muted-foreground">Hasta qué fecha se trajeron los datos de cada tabla.</p>
            </div>
            <Link
              href={`/viewer?type=watermarks&cartridge=${encodeURIComponent(cartridge)}`}
              className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Ver detalle
            </Link>
          </div>
          {freshness.isError ? (
            <ErrorPanel message="No se pudo cargar la última actualización." onRetry={() => freshness.refetch()} />
          ) : (
            <div className="space-y-2">
              {watermarks.slice(0, 8).map((row) => (
                <div key={row.entity} className="flex items-center justify-between gap-3 rounded-md border bg-background px-3 py-2">
                  <div>
                    <div className="text-sm font-medium">{row.entity}</div>
                    <div className="text-xs text-muted-foreground">{row.watermark_value || "sin datos aún"}</div>
                  </div>
                  <StatusPill status={row.last_run_status || "unknown"} />
                </div>
              ))}
              {!watermarks.length && !freshness.isLoading ? (
                <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">Todavía no hay datos cargados.</p>
              ) : null}
            </div>
          )}
        </div>
      </section>

      <section className="space-y-3" aria-label="Tareas recientes">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold">Tareas recientes</h2>
            <p className="text-xs text-muted-foreground">Historial de ejecuciones y enlaces a los registros.</p>
          </div>
          <Link
            href="/viewer?type=jobs"
            className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Abrir historial
          </Link>
        </div>
        {jobs.isError ? (
          <ErrorPanel message="No se pudieron cargar las tareas." onRetry={() => jobs.refetch()} />
        ) : jobs.isLoading ? (
          <SkeletonRows />
        ) : (
          <JobTable jobs={jobs.data ?? []} />
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
