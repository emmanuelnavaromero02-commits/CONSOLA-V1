"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, type ReactNode } from "react";
import { ArrowLeft, Database, Droplets, FileText, Layers3, ListChecks, RefreshCcw } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { JobTable } from "@/components/monitor/JobTable";
import { PipelineTable } from "@/components/monitor/PipelineTable";
import { StatusPill } from "@/components/monitor/StatusPill";
import { useFreshness, useJob, useJobLogs, useJobs, usePipeline, useSemantic } from "@/lib/monitor/hooks";
import type { FreshnessEntity, JobLogLine, JobRun, SemanticEntity, SemanticPayload } from "@/lib/monitor/types";

const DEFAULT_CARTRIDGE = "replicon";

type ViewerType = "jobs" | "job" | "pipeline" | "watermarks" | "semantic";

const VIEWER_LINKS: Array<{ type: ViewerType; label: string; icon: LucideIcon }> = [
  { type: "jobs", label: "Jobs", icon: ListChecks },
  { type: "pipeline", label: "Pipeline", icon: Layers3 },
  { type: "watermarks", label: "Watermarks", icon: Droplets },
  { type: "semantic", label: "Semantic", icon: Database },
];

export default function ViewerPage() {
  return (
    <Suspense fallback={<ViewerShell title="Viewer" subtitle="Cargando parámetros..." />}>
      <ViewerContent />
    </Suspense>
  );
}

function ViewerContent() {
  const params = useSearchParams();
  const type = normaliseType(params.get("type"));
  const cartridge = params.get("cartridge") || DEFAULT_CARTRIDGE;
  const jobId = params.get("id") || params.get("job_id");

  if (type === "job") {
    return <JobViewer jobId={jobId} />;
  }
  if (type === "pipeline") {
    return <PipelineViewer cartridge={cartridge} />;
  }
  if (type === "watermarks") {
    return <WatermarksViewer cartridge={cartridge} />;
  }
  if (type === "semantic") {
    return <SemanticViewer cartridge={cartridge} />;
  }
  return <JobsViewer />;
}

function normaliseType(value: string | null): ViewerType {
  if (value === "job" || value === "logs") return "job";
  if (value === "pipeline") return "pipeline";
  if (value === "watermarks" || value === "freshness") return "watermarks";
  if (value === "semantic" || value === "semantic-layer") return "semantic";
  return "jobs";
}

function ViewerShell({
  title,
  subtitle,
  children,
  actions,
}: {
  title: string;
  subtitle?: string;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-8">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-2">
          <Link
            href="/monitor"
            className="inline-flex min-h-[44px] items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ArrowLeft aria-hidden className="h-4 w-4" />
            Monitor
          </Link>
          <div className="space-y-1">
            <h1 className="text-3xl font-semibold tracking-tight">{title}</h1>
            {subtitle ? <p className="text-sm text-muted-foreground">{subtitle}</p> : null}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {actions}
          <ViewerSwitcher />
        </div>
      </header>
      {children}
    </main>
  );
}

function ViewerSwitcher() {
  return (
    <nav aria-label="Viewers" className="flex flex-wrap gap-2">
      {VIEWER_LINKS.map(({ type, label, icon: Icon }) => (
        <Link
          key={type}
          href={type === "jobs" ? "/viewer?type=jobs" : `/viewer?type=${type}&cartridge=${DEFAULT_CARTRIDGE}`}
          className="inline-flex min-h-[44px] items-center gap-2 rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Icon aria-hidden className="h-4 w-4" />
          {label}
        </Link>
      ))}
    </nav>
  );
}

function JobsViewer() {
  const jobs = useJobs(100);
  return (
    <ViewerShell
      title="Jobs"
      subtitle="Historial reciente de ejecución y entrada directa a logs."
      actions={<RefreshButton onClick={() => jobs.refetch()} />}
    >
      {jobs.isError ? (
        <ErrorPanel message="No se pudieron cargar los jobs." onRetry={() => jobs.refetch()} />
      ) : jobs.isLoading ? (
        <SkeletonRows />
      ) : (
        <JobTable jobs={jobs.data ?? []} />
      )}
    </ViewerShell>
  );
}

function JobViewer({ jobId }: { jobId: string | null }) {
  const job = useJob(jobId);
  const logs = useJobLogs(jobId);

  if (!jobId) {
    return (
      <ViewerShell title="Job" subtitle="Falta el parámetro id.">
        <EmptyPanel
          icon={FileText}
          title="Selecciona un job"
          detail="Abre este viewer desde el historial para cargar los logs de una ejecución concreta."
        />
      </ViewerShell>
    );
  }

  return (
    <ViewerShell
      title={`Job ${jobId}`}
      subtitle="Detalle, resultado y líneas de log de la ejecución."
      actions={<RefreshButton onClick={() => { job.refetch(); logs.refetch(); }} />}
    >
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[0.7fr_1.3fr]">
        <section className="space-y-3 rounded-lg border bg-card p-4" aria-label="Detalle del job">
          <h2 className="text-base font-semibold">Detalle</h2>
          {job.isError ? (
            <ErrorPanel message="No se pudo cargar el job." onRetry={() => job.refetch()} />
          ) : job.isLoading ? (
            <SkeletonRows rows={4} />
          ) : (
            <JobDetails job={job.data} />
          )}
        </section>
        <section className="space-y-3 rounded-lg border bg-card p-4" aria-label="Logs del job">
          <h2 className="text-base font-semibold">Logs</h2>
          {logs.isError ? (
            <ErrorPanel message="No se pudieron cargar los logs." onRetry={() => logs.refetch()} />
          ) : logs.isLoading ? (
            <SkeletonRows rows={8} />
          ) : (
            <JobLogTable logs={logs.data ?? []} />
          )}
        </section>
      </div>
    </ViewerShell>
  );
}

function PipelineViewer({ cartridge }: { cartridge: string }) {
  const pipeline = usePipeline(cartridge);
  return (
    <ViewerShell
      title="Pipeline"
      subtitle={`Cartucho ${cartridge}: bronze, silver, gold y última corrida.`}
      actions={<RefreshButton onClick={() => pipeline.refetch()} />}
    >
      {pipeline.isError ? (
        <ErrorPanel message="No se pudo cargar el pipeline." onRetry={() => pipeline.refetch()} />
      ) : pipeline.isLoading ? (
        <SkeletonRows />
      ) : (
        <PipelineTable rows={pipeline.data ?? []} />
      )}
    </ViewerShell>
  );
}

function WatermarksViewer({ cartridge }: { cartridge: string }) {
  const freshness = useFreshness(cartridge);
  return (
    <ViewerShell
      title="Watermarks"
      subtitle={`Cartucho ${cartridge}: marcas de agua y último estado por entidad.`}
      actions={<RefreshButton onClick={() => freshness.refetch()} />}
    >
      {freshness.isError ? (
        <ErrorPanel message="No se pudieron cargar las marcas de agua." onRetry={() => freshness.refetch()} />
      ) : freshness.isLoading ? (
        <SkeletonRows />
      ) : (
        <WatermarksTable rows={freshness.data ?? []} />
      )}
    </ViewerShell>
  );
}

function SemanticViewer({ cartridge }: { cartridge: string }) {
  const semantic = useSemantic(cartridge);
  const entities = useMemo(() => flattenSemantic(semantic.data), [semantic.data]);

  return (
    <ViewerShell
      title="Semantic Layer"
      subtitle={`Cartucho ${cartridge}: entidades, campos y metadatos expuestos por /api/semantic.`}
      actions={<RefreshButton onClick={() => semantic.refetch()} />}
    >
      {semantic.isError ? (
        <ErrorPanel message="No se pudo cargar la capa semántica." onRetry={() => semantic.refetch()} />
      ) : semantic.isLoading ? (
        <SkeletonRows />
      ) : (
        <SemanticTable rows={entities} />
      )}
    </ViewerShell>
  );
}

function RefreshButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex min-h-[44px] items-center gap-2 rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <RefreshCcw aria-hidden className="h-4 w-4" />
      Refrescar
    </button>
  );
}

function JobDetails({ job }: { job: JobRun | undefined }) {
  if (!job) return <EmptyPanel icon={FileText} title="Sin detalle" detail="El backend no devolvió datos para este job." />;
  const result = job.result && Object.keys(job.result).length > 0 ? job.result : null;
  const args = job.args && Object.keys(job.args).length > 0 ? job.args : null;

  return (
    <div className="space-y-4 text-sm">
      <dl className="grid grid-cols-1 gap-3">
        <DetailItem label="Estado" value={<StatusPill status={job.status} />} />
        <DetailItem label="Tool" value={job.tool || "-"} />
        <DetailItem label="Mensaje" value={job.message || "-"} />
        <DetailItem label="Creado" value={formatDate(job.created_at)} />
        <DetailItem label="Inicio" value={formatDate(job.started_at)} />
        <DetailItem label="Fin" value={formatDate(job.finished_at)} />
      </dl>
      {args ? <JsonBlock label="Args" value={args} /> : null}
      {result ? <JsonBlock label="Resultado" value={result} /> : null}
    </div>
  );
}

function DetailItem({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-md border bg-background p-3">
      <dt className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{label}</dt>
      <dd className="mt-1 break-words">{value}</dd>
    </div>
  );
}

function JobLogTable({ logs }: { logs: JobLogLine[] }) {
  if (!logs.length) return <EmptyPanel icon={FileText} title="Sin logs" detail="No hay líneas de log para esta ejecución." />;
  return (
    <div className="overflow-x-auto rounded-lg border bg-background">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Tiempo</th>
            <th className="px-3 py-2 font-medium">Nivel</th>
            <th className="px-3 py-2 font-medium">Entidad</th>
            <th className="px-3 py-2 font-medium">Mensaje</th>
          </tr>
        </thead>
        <tbody>
          {logs.map((line, index) => (
            <tr key={`${line.ts || "log"}:${index}`} className="border-t">
              <td className="px-3 py-2 align-top font-mono text-xs text-muted-foreground">{formatDate(line.ts)}</td>
              <td className="px-3 py-2 align-top"><StatusPill status={line.level || "info"} /></td>
              <td className="px-3 py-2 align-top">{line.entity || "-"}</td>
              <td className="px-3 py-2 align-top">
                <div className="max-w-3xl whitespace-pre-wrap break-words">{line.message || line.detail || "-"}</div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function WatermarksTable({ rows }: { rows: FreshnessEntity[] }) {
  if (!rows.length) {
    return <EmptyPanel icon={Droplets} title="Sin watermarks" detail="El backend no devolvió marcas para este cartucho." />;
  }
  return (
    <div className="overflow-x-auto rounded-lg border bg-card">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Entidad</th>
            <th className="px-3 py-2 font-medium">Watermark</th>
            <th className="px-3 py-2 font-medium">Actualizado</th>
            <th className="px-3 py-2 font-medium">Último estado</th>
            <th className="px-3 py-2 font-medium">Última corrida</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.entity} className="border-t">
              <td className="px-3 py-2 align-top font-medium">{row.entity}</td>
              <td className="px-3 py-2 align-top font-mono text-xs text-muted-foreground">{row.watermark_value || "-"}</td>
              <td className="px-3 py-2 align-top text-xs">{formatDate(row.watermark_updated_at)}</td>
              <td className="px-3 py-2 align-top"><StatusPill status={row.last_run_status || "unknown"} /></td>
              <td className="px-3 py-2 align-top text-xs">{formatDate(row.last_run_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SemanticTable({ rows }: { rows: SemanticEntity[] }) {
  if (!rows.length) {
    return <EmptyPanel icon={Database} title="Sin entidades semánticas" detail="El backend no devolvió entidades para este cartucho." />;
  }
  return (
    <div className="overflow-x-auto rounded-lg border bg-card">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Entidad</th>
            <th className="px-3 py-2 font-medium">Capa</th>
            <th className="px-3 py-2 font-medium">Watermark</th>
            <th className="px-3 py-2 font-medium">Campos</th>
            <th className="px-3 py-2 font-medium">Descripción</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const name = row.name || row.entity || `entity-${index + 1}`;
            return (
              <tr key={`${name}:${index}`} className="border-t">
                <td className="px-3 py-2 align-top font-medium">{name}</td>
                <td className="px-3 py-2 align-top text-xs text-muted-foreground">{row.layer || "-"}</td>
                <td className="px-3 py-2 align-top font-mono text-xs text-muted-foreground">
                  {row.watermark || row.last_watermark || row.watermark_field || "-"}
                </td>
                <td className="px-3 py-2 align-top text-xs">{Array.isArray(row.fields) ? row.fields.length : 0}</td>
                <td className="px-3 py-2 align-top text-xs text-muted-foreground">{row.description || "-"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function flattenSemantic(payload: SemanticPayload | undefined): SemanticEntity[] {
  if (!payload?.entities) return [];
  if (Array.isArray(payload.entities)) return payload.entities;
  return Object.values(payload.entities).flat();
}

function JsonBlock({ label, value }: { label: string; value: Record<string, unknown> }) {
  return (
    <section className="space-y-2">
      <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{label}</h3>
      <pre className="max-h-72 overflow-auto rounded-md border bg-background p-3 text-xs">
{JSON.stringify(value, null, 2)}
      </pre>
    </section>
  );
}

function EmptyPanel({ icon: Icon, title, detail }: { icon: LucideIcon; title: string; detail: string }) {
  return (
    <div className="rounded-lg border bg-muted/20 p-6 text-sm">
      <div className="flex items-start gap-3">
        <span aria-hidden className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-background text-muted-foreground">
          <Icon className="h-5 w-5" />
        </span>
        <div className="space-y-1">
          <h2 className="font-semibold">{title}</h2>
          <p className="text-muted-foreground">{detail}</p>
        </div>
      </div>
    </div>
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

function SkeletonRows({ rows = 6 }: { rows?: number }) {
  return (
    <div aria-busy="true" className="space-y-2">
      {Array.from({ length: rows }).map((_, index) => (
        <span key={index} className="block h-12 animate-pulse rounded bg-muted" aria-hidden />
      ))}
    </div>
  );
}

function formatDate(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
