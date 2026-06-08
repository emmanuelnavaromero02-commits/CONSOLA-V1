"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  Database,
  Droplets,
  FileText,
  GitBranch,
  KeyRound,
  Layers3,
  ListChecks,
  Network,
  RefreshCcw,
  Search,
  ShieldCheck,
  Table2,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { JobTable } from "@/components/monitor/JobTable";
import { PipelineTable } from "@/components/monitor/PipelineTable";
import { StatusPill } from "@/components/monitor/StatusPill";
import { cn } from "@/lib/utils";
import {
  useDatasetDetail,
  useDatasetLineage,
  useDatasetPreview,
  useDatasets,
  useFreshness,
  useJob,
  useJobLogs,
  useJobs,
  useLineage,
  usePipeline,
  useSemantic,
  useSourceSchema,
  useSources,
  useVaultConnections,
  useVaultSecrets,
} from "@/lib/monitor/hooks";
import type {
  DataRow,
  DatasetDetail,
  DatasetLineageRow,
  DatasetSummary,
  FreshnessEntity,
  JobLogLine,
  JobRun,
  LineageEdge,
  LineageNode,
  SemanticEntity,
  SemanticPayload,
  SourceSchemaPayload,
  VaultConnection,
  VaultSecret,
} from "@/lib/monitor/types";

const DEFAULT_CARTRIDGE = "sap_successfactors";

type ViewerType =
  | "jobs"
  | "job"
  | "pipeline"
  | "watermarks"
  | "semantic"
  | "schema"
  | "datasets"
  | "dataset"
  | "lineage"
  | "vault";

const VIEWER_LINKS: Array<{ type: ViewerType; label: string; icon: LucideIcon }> = [
  { type: "jobs", label: "Jobs", icon: ListChecks },
  { type: "pipeline", label: "Pipeline", icon: Layers3 },
  { type: "watermarks", label: "Watermarks", icon: Droplets },
  { type: "semantic", label: "Semantic", icon: Database },
  { type: "datasets", label: "Datasets", icon: Table2 },
  { type: "schema", label: "Schema", icon: Search },
  { type: "lineage", label: "Lineage", icon: GitBranch },
  { type: "vault", label: "Vault", icon: KeyRound },
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
  const activeCartridges = useActiveScopedCartridges();
  const requestedCartridge = params.get("cartridge") || DEFAULT_CARTRIDGE;
  const cartridge = resolveScopedCartridge(requestedCartridge, activeCartridges);
  const jobId = params.get("id") || params.get("job_id");
  const datasetName = params.get("name") || params.get("dataset") || params.get("id");
  const source = params.get("source");
  const scope = type === "vault" ? cartridge : (params.get("scope") || cartridge);

  useEffect(() => {
    if (!activeCartridges.length || requestedCartridge === cartridge || !cartridgeViewerUsesCartridge(type)) return;
    const nextParams = new URLSearchParams(window.location.search);
    nextParams.set("cartridge", cartridge);
    if (type === "vault") nextParams.set("scope", cartridge);
    window.history.replaceState(null, "", `${window.location.pathname}?${nextParams.toString()}`);
  }, [activeCartridges.length, cartridge, requestedCartridge, type]);

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
  if (type === "schema") {
    return <SchemaViewer source={source} />;
  }
  if (type === "datasets") {
    return <DatasetsViewer />;
  }
  if (type === "dataset") {
    return <DatasetViewer name={datasetName} />;
  }
  if (type === "lineage") {
    return <LineageViewer cartridge={params.get("cartridge") ? cartridge : ""} />;
  }
  if (type === "vault") {
    return <VaultViewer cartridge={cartridge} scope={scope} />;
  }
  return <JobsViewer />;
}

function normaliseType(value: string | null): ViewerType {
  if (value === "job" || value === "logs") return "job";
  if (value === "pipeline") return "pipeline";
  if (value === "watermarks" || value === "freshness") return "watermarks";
  if (value === "semantic" || value === "semantic-layer") return "semantic";
  if (value === "schema") return "schema";
  if (value === "datasets") return "datasets";
  if (value === "dataset") return "dataset";
  if (value === "lineage") return "lineage";
  if (value === "vault") return "vault";
  return "jobs";
}

function cartridgeViewerUsesCartridge(type: ViewerType): boolean {
  return type === "pipeline" || type === "watermarks" || type === "semantic" || type === "vault" || type === "lineage";
}

function resolveScopedCartridge(requested: string, activeCartridges: string[]): string {
  const cleanRequested = requested.trim() || DEFAULT_CARTRIDGE;
  if (activeCartridges.length === 0) return cleanRequested;
  return activeCartridges.includes(cleanRequested) ? cleanRequested : activeCartridges[0] || DEFAULT_CARTRIDGE;
}

function useActiveScopedCartridges(): string[] {
  const [active, setActive] = useState<string[]>([]);
  useEffect(() => {
    let cancelled = false;
    fetch("/api/apps", { credentials: "same-origin" })
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => {
        if (cancelled) return;
        const cartridges = Array.isArray(payload?.active_scoped_cartridges)
          ? payload.active_scoped_cartridges.filter((item: unknown): item is string => typeof item === "string" && item.trim().length > 0)
          : [];
        setActive(cartridges);
      })
      .catch(() => {
        if (!cancelled) setActive([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return active;
}

function ViewerShell({
  title,
  subtitle,
  children,
  actions,
  activeCartridge,
}: {
  title: string;
  subtitle?: string;
  children?: ReactNode;
  actions?: ReactNode;
  activeCartridge?: string;
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
          <ViewerSwitcher activeCartridge={activeCartridge} />
        </div>
      </header>
      {children}
    </main>
  );
}

function ViewerSwitcher({ activeCartridge = DEFAULT_CARTRIDGE }: { activeCartridge?: string }) {
  const activeCartridges = useActiveScopedCartridges();
  const cartridge = resolveScopedCartridge(activeCartridge, activeCartridges);
  return (
    <nav aria-label="Viewers" className="flex flex-wrap gap-2">
      {VIEWER_LINKS.map(({ type, label, icon: Icon }) => (
        <Link
          key={type}
          href={viewerHref(type, cartridge)}
          className="inline-flex min-h-[44px] items-center gap-2 rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Icon aria-hidden className="h-4 w-4" />
          {label}
        </Link>
      ))}
    </nav>
  );
}

function viewerHref(type: ViewerType, cartridge = DEFAULT_CARTRIDGE): string {
  if (type === "jobs") return "/viewer?type=jobs";
  if (type === "schema") return "/viewer?type=schema";
  if (type === "datasets") return "/viewer?type=datasets";
  if (type === "lineage") return "/viewer?type=lineage";
  if (type === "vault") return `/viewer?type=vault&cartridge=${encodeURIComponent(cartridge)}&scope=${encodeURIComponent(cartridge)}`;
  return `/viewer?type=${type}&cartridge=${encodeURIComponent(cartridge)}`;
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
      activeCartridge={cartridge}
    >
      {pipeline.isError ? (
        <ErrorPanel message="No se pudo cargar el pipeline." onRetry={() => pipeline.refetch()} />
      ) : pipeline.isLoading ? (
        <SkeletonRows />
      ) : (
        <PipelineTable
          rows={pipeline.data ?? []}
          cartridge={cartridge}
          onExtractionStarted={() => pipeline.refetch()}
        />
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
      activeCartridge={cartridge}
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
      activeCartridge={cartridge}
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

function SchemaViewer({ source }: { source: string | null }) {
  const sources = useSources();
  const [selectedSource, setSelectedSource] = useState(source || "");
  const schema = useSourceSchema(selectedSource || null);

  return (
    <ViewerShell
      title="Schema"
      subtitle="Particiones, columnas inferidas y preview seguro de una fuente Bronze."
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={selectedSource}
            onChange={(event) => setSelectedSource(event.target.value)}
            className="min-h-[44px] min-w-64 rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Fuente"
          >
            <option value="">Selecciona una fuente</option>
            {(sources.data ?? []).map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
          <RefreshButton onClick={() => { sources.refetch(); schema.refetch(); }} />
        </div>
      }
    >
      {sources.isError ? (
        <ErrorPanel message="No se pudieron cargar las fuentes." onRetry={() => sources.refetch()} />
      ) : !selectedSource ? (
        <EmptyPanel icon={Search} title="Selecciona una fuente" detail="Elige una fuente para inspeccionar particiones, columnas y muestra." />
      ) : schema.isError ? (
        <ErrorPanel message="No se pudo cargar el schema." onRetry={() => schema.refetch()} />
      ) : schema.isLoading ? (
        <SkeletonRows />
      ) : (
        <SchemaPanel payload={schema.data} />
      )}
    </ViewerShell>
  );
}

function DatasetsViewer() {
  const datasets = useDatasets();
  const [layer, setLayer] = useState("");
  const [cartridge, setCartridge] = useState("");
  const rows = useMemo(() => datasets.data ?? [], [datasets.data]);
  const cartridges = useMemo(
    () => Array.from(new Set(rows.map((row) => row.cartridge).filter(Boolean) as string[])).sort(),
    [rows],
  );
  const filtered = useMemo(
    () => rows.filter((row) => (!layer || (row.layer || "silver") === layer) && (!cartridge || row.cartridge === cartridge)),
    [cartridge, layer, rows],
  );

  return (
    <ViewerShell
      title="Datasets"
      subtitle="Inventario Silver/Gold con filtros de capa y cartucho."
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={layer}
            onChange={(event) => setLayer(event.target.value)}
            className="min-h-[44px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Capa"
          >
            <option value="">Todas las capas</option>
            <option value="silver">Silver</option>
            <option value="gold">Gold</option>
          </select>
          <select
            value={cartridge}
            onChange={(event) => setCartridge(event.target.value)}
            className="min-h-[44px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label="Cartucho"
          >
            <option value="">Todos los cartuchos</option>
            {cartridges.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
          <RefreshButton onClick={() => datasets.refetch()} />
        </div>
      }
    >
      {datasets.isError ? (
        <ErrorPanel message="No se pudieron cargar los datasets." onRetry={() => datasets.refetch()} />
      ) : datasets.isLoading ? (
        <SkeletonRows />
      ) : (
        <DatasetTable rows={filtered} />
      )}
    </ViewerShell>
  );
}

function DatasetViewer({ name }: { name: string | null }) {
  const detail = useDatasetDetail(name);
  const preview = useDatasetPreview(name, 20);
  const lineage = useDatasetLineage(name);

  if (!name) {
    return (
      <ViewerShell title="Dataset" subtitle="Falta el parámetro name.">
        <EmptyPanel icon={Table2} title="Selecciona un dataset" detail="Abre este viewer desde el inventario de datasets para cargar metadata y preview." />
      </ViewerShell>
    );
  }

  return (
    <ViewerShell
      title={`Dataset ${name}`}
      subtitle="Definición, mapeo, lineage y preview con RLS aplicado por backend."
      actions={<RefreshButton onClick={() => { detail.refetch(); preview.refetch(); lineage.refetch(); }} />}
    >
      {detail.isError ? (
        <ErrorPanel message="No se pudo cargar el dataset." onRetry={() => detail.refetch()} />
      ) : detail.isLoading ? (
        <SkeletonRows />
      ) : (
        <div className="space-y-4">
          <DatasetSummaryPanel detail={detail.data} fallbackName={name} />
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[0.85fr_1.15fr]">
            <section className="space-y-3 rounded-lg border bg-card p-4">
              <h2 className="text-base font-semibold">Lineage</h2>
              {lineage.isError ? (
                <ErrorPanel message="No se pudo cargar el lineage del dataset." onRetry={() => lineage.refetch()} />
              ) : lineage.isLoading ? (
                <SkeletonRows rows={4} />
              ) : (
                <DatasetLineageTable rows={lineage.data ?? []} />
              )}
            </section>
            <section className="space-y-3 rounded-lg border bg-card p-4">
              <h2 className="text-base font-semibold">Preview</h2>
              {preview.isError ? (
                <ErrorPanel message="No se pudo cargar el preview." onRetry={() => preview.refetch()} />
              ) : preview.isLoading ? (
                <SkeletonRows rows={4} />
              ) : (
                <PreviewTable rows={preview.data ?? []} />
              )}
            </section>
          </div>
        </div>
      )}
    </ViewerShell>
  );
}

function LineageViewer({ cartridge }: { cartridge: string }) {
  const lineage = useLineage(cartridge || undefined);

  return (
    <ViewerShell
      title="Lineage"
      subtitle={cartridge ? `Grafo de datasets para ${cartridge}.` : "Grafo global de fuentes raw y datasets Silver/Gold."}
      actions={<RefreshButton onClick={() => lineage.refetch()} />}
      activeCartridge={cartridge || DEFAULT_CARTRIDGE}
    >
      {lineage.isError ? (
        <ErrorPanel message="No se pudo cargar el lineage." onRetry={() => lineage.refetch()} />
      ) : lineage.isLoading ? (
        <SkeletonRows />
      ) : (
        <LineagePanel nodes={lineage.data?.nodes ?? []} edges={lineage.data?.edges ?? []} />
      )}
    </ViewerShell>
  );
}

function VaultViewer({ cartridge, scope }: { cartridge: string; scope: string }) {
  const connections = useVaultConnections(cartridge);
  const secrets = useVaultSecrets(scope);

  return (
    <ViewerShell
      title="Vault"
      subtitle={`Conexiones de ${cartridge} y secretos masked del scope ${scope}.`}
      actions={<RefreshButton onClick={() => { connections.refetch(); secrets.refetch(); }} />}
      activeCartridge={cartridge}
    >
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <section className="space-y-3 rounded-lg border bg-card p-4">
          <h2 className="text-base font-semibold">Conexiones</h2>
          {connections.isError ? (
            <ErrorPanel message="No se pudieron cargar conexiones de Vault." onRetry={() => connections.refetch()} />
          ) : connections.isLoading ? (
            <SkeletonRows rows={4} />
          ) : (
            <VaultConnectionsTable rows={connections.data ?? []} />
          )}
        </section>
        <section className="space-y-3 rounded-lg border bg-card p-4">
          <h2 className="text-base font-semibold">Secretos masked</h2>
          {secrets.isError ? (
            <ErrorPanel message="No se pudieron cargar secretos masked." onRetry={() => secrets.refetch()} />
          ) : secrets.isLoading ? (
            <SkeletonRows rows={4} />
          ) : (
            <VaultSecretsTable rows={secrets.data ?? []} />
          )}
        </section>
      </div>
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

function SchemaPanel({ payload }: { payload: SourceSchemaPayload | undefined }) {
  const partitions = payload?.partitions?.partitions ?? [];
  const latest = payload?.partitions?.latest;
  const sqlLatest = payload?.partitions?.sql_latest;
  const rows = payload?.preview?.rows ?? payload?.preview?.data ?? payload?.preview?.result ?? [];
  const schemaColumns = (payload?.preview?.schema ?? [])
    .map((column) => column.name)
    .filter((name): name is string => Boolean(name));
  const columns = payload?.preview?.columns ?? (schemaColumns.length ? schemaColumns : columnsFromRows(rows));

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[0.8fr_1.2fr]">
      <section className="space-y-3 rounded-lg border bg-card p-4">
        <h2 className="text-base font-semibold">Particiones</h2>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <MetricBox label="Total" value={partitions.length} />
          <MetricBox label="Última" value={latest || "-"} />
        </div>
        {partitions.length ? (
          <div className="flex flex-wrap gap-2">
            {partitions.slice(-24).reverse().map((partition) => (
              <span
                key={partition}
                className="rounded-full border bg-background px-2 py-1 font-mono text-[11px] text-muted-foreground"
              >
                {partition}
              </span>
            ))}
          </div>
        ) : (
          <EmptyPanel icon={Database} title="Sin particiones" detail="El backend no devolvió particiones para esta fuente." />
        )}
        {sqlLatest ? <JsonBlock label="SQL última partición" value={{ sql_latest: sqlLatest }} /> : null}
      </section>
      <section className="space-y-3 rounded-lg border bg-card p-4">
        <h2 className="text-base font-semibold">Columnas y preview</h2>
        <ColumnTable columns={columns} sample={rows[0]} />
        <PreviewTable rows={rows.slice(0, 5)} />
      </section>
    </div>
  );
}

function DatasetTable({ rows }: { rows: DatasetSummary[] }) {
  if (!rows.length) {
    return <EmptyPanel icon={Table2} title="Sin datasets" detail="No hay datasets visibles con los filtros actuales." />;
  }
  return (
    <div className="overflow-x-auto rounded-lg border bg-card">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Nombre</th>
            <th className="px-3 py-2 font-medium">Capa</th>
            <th className="px-3 py-2 font-medium">Cartucho</th>
            <th className="px-3 py-2 font-medium">Fuente</th>
            <th className="px-3 py-2 font-medium">Columnas</th>
            <th className="px-3 py-2 font-medium">Estado</th>
            <th className="px-3 py-2 font-medium">Detalle</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.name} className="border-t">
              <td className="px-3 py-2 align-top font-mono text-xs font-medium">{row.name}</td>
              <td className="px-3 py-2 align-top"><LayerPill layer={row.layer} /></td>
              <td className="px-3 py-2 align-top text-xs text-muted-foreground">{row.cartridge || "-"}</td>
              <td className="px-3 py-2 align-top text-xs">{row.source_entity || (row.sources ?? [])[0] || "-"}</td>
              <td className="px-3 py-2 align-top text-xs">{row.column_count ?? (Object.keys(row.column_mapping ?? {}).length || "-")}</td>
              <td className="px-3 py-2 align-top">
                <StatusPill status={row.is_stale ? "stale" : "fresh"} />
                {row.staleness_reason ? <div className="mt-1 text-xs text-muted-foreground">{row.staleness_reason}</div> : null}
              </td>
              <td className="px-3 py-2 align-top">
                <Link
                  href={`/viewer?type=dataset&name=${encodeURIComponent(row.name)}`}
                  className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  Abrir
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DatasetSummaryPanel({ detail, fallbackName }: { detail: DatasetDetail | undefined; fallbackName: string }) {
  const mapping = Object.entries(detail?.column_mapping ?? {});
  return (
    <section className="space-y-4 rounded-lg border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">{detail?.name || fallbackName}</h2>
          <p className="text-xs text-muted-foreground">
            {detail?.cartridge || "sin cartucho"} · {detail?.source_entity || "sin fuente"} · {formatDate(detail?.updated_at || detail?.last_refresh)}
          </p>
        </div>
        <LayerPill layer={detail?.layer} />
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <DetailItem label="Columnas" value={mapping.length || detail?.column_count || 0} />
        <DetailItem label="Fecha fuente" value={detail?.source_load_date || "-"} />
        <DetailItem label="Batch fuente" value={detail?.source_batch_id || "-"} />
      </div>
      {detail?.sql ? (
        <section className="space-y-2">
          <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">SQL</h3>
          <pre className="max-h-72 overflow-auto rounded-md border bg-background p-3 text-xs">{detail.sql}</pre>
        </section>
      ) : null}
      {mapping.length ? (
        <section className="space-y-2">
          <h3 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Mapeo</h3>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {mapping.slice(0, 16).map(([source, business]) => (
              <div key={source} className="rounded-md border bg-background p-3 text-xs">
                <span className="font-mono text-muted-foreground">{source}</span>
                <span className="px-2 text-muted-foreground">→</span>
                <span className="font-medium">{business}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </section>
  );
}

function DatasetLineageTable({ rows }: { rows: DatasetLineageRow[] }) {
  if (!rows.length) {
    return <EmptyPanel icon={GitBranch} title="Sin lineage" detail="No hay historial de materialización para este dataset." />;
  }
  return (
    <div className="overflow-x-auto rounded-lg border bg-background">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Fecha</th>
            <th className="px-3 py-2 font-medium">Batch</th>
            <th className="px-3 py-2 font-medium">Filas</th>
            <th className="px-3 py-2 font-medium">Storage</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.source_batch_id || "batch"}:${index}`} className="border-t">
              <td className="px-3 py-2 align-top text-xs">{formatDate(row.created_at)}</td>
              <td className="px-3 py-2 align-top font-mono text-xs">{row.source_batch_id || "-"}</td>
              <td className="px-3 py-2 align-top text-xs">{row.row_count ?? "-"}</td>
              <td className="px-3 py-2 align-top text-xs text-muted-foreground">{row.storage_uri || "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LineagePanel({ nodes, edges }: { nodes: LineageNode[]; edges: LineageEdge[] }) {
  const [selectedId, setSelectedId] = useState<string | null>(nodes[0]?.id ?? null);
  const [layerFilter, setLayerFilter] = useState("all");
  const [cartridgeFilter, setCartridgeFilter] = useState("all");

  if (!nodes.length) {
    return <EmptyPanel icon={GitBranch} title="Sin lineage" detail="No hay nodos visibles para el filtro actual." />;
  }

  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const cartridges = Array.from(new Set(nodes.map((node) => node.cartridge).filter(Boolean) as string[])).sort();
  const visibleNodes = nodes.filter((node) => (
    (layerFilter === "all" || normaliseLayer(node.type) === layerFilter) &&
    (cartridgeFilter === "all" || node.cartridge === cartridgeFilter)
  ));
  const visibleIds = new Set(visibleNodes.map((node) => node.id));
  const visibleEdges = edges.filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to));
  const selectedNode = selectedId && visibleIds.has(selectedId) ? nodeById.get(selectedId) : visibleNodes[0];
  const incoming = selectedNode ? edges.filter((edge) => edge.to === selectedNode.id) : [];
  const outgoing = selectedNode ? edges.filter((edge) => edge.from === selectedNode.id) : [];
  const upstreamIds = selectedNode ? collectReachable(selectedNode.id, edges, "upstream") : new Set<string>();
  const downstreamIds = selectedNode ? collectReachable(selectedNode.id, edges, "downstream") : new Set<string>();
  const chart = layoutLineageGraph(visibleNodes, visibleEdges);

  return (
    <div className="space-y-4">
      <section className="rounded-lg border bg-card p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
            <MetricBox label="Nodos" value={visibleNodes.length} />
            <MetricBox label="Dependencias" value={visibleEdges.length} />
            <MetricBox label="Gold" value={visibleNodes.filter((node) => normaliseLayer(node.type) === "gold").length} />
            <MetricBox label="Stale" value={visibleNodes.filter((node) => node.is_stale).length} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label className="text-xs font-medium text-muted-foreground">
              Cartucho
              <select
                value={cartridgeFilter}
                onChange={(event) => {
                  setCartridgeFilter(event.target.value);
                  setSelectedId(null);
                }}
                className="ml-2 min-h-[40px] rounded-md border bg-background px-2 text-sm text-foreground"
              >
                <option value="all">Todos</option>
                {cartridges.map((cartridge) => (
                  <option key={cartridge} value={cartridge}>{cartridge}</option>
                ))}
              </select>
            </label>
            <label className="text-xs font-medium text-muted-foreground">
              Capa
              <select
                value={layerFilter}
                onChange={(event) => {
                  setLayerFilter(event.target.value);
                  setSelectedId(null);
                }}
                className="ml-2 min-h-[40px] rounded-md border bg-background px-2 text-sm text-foreground"
              >
                <option value="all">Todas</option>
                <option value="raw">raw</option>
                <option value="silver">silver</option>
                <option value="gold">gold</option>
              </select>
            </label>
          </div>
        </div>
      </section>

      <section className="overflow-hidden rounded-lg border bg-card" aria-label="Grafo operativo de linaje">
        <div className="border-b px-4 py-3">
          <h2 className="text-base font-semibold">Grafo operativo</h2>
          <p className="mt-1 text-xs text-muted-foreground">Selecciona un nodo para ver entradas, salidas e impacto.</p>
        </div>
        <div className="overflow-auto bg-muted/20 p-3">
          <svg
            className="min-h-[520px] w-full"
            viewBox={`0 0 ${chart.width} ${chart.height}`}
            role="img"
            aria-label="Grafo de linaje con flechas direccionales"
          >
            <defs>
              <marker
                id="lineage-arrow-muted"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="7"
                markerHeight="7"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" className="fill-muted-foreground" />
              </marker>
              <marker
                id="lineage-arrow-impact"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="8"
                markerHeight="8"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" className="fill-primary" />
              </marker>
              <marker
                id="lineage-arrow-source"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="8"
                markerHeight="8"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" className="fill-warning" />
              </marker>
            </defs>
            {chart.laneLabels.map((lane) => (
              <g key={lane.label}>
                <text x={lane.x} y={32} className="fill-muted-foreground text-[12px] font-semibold uppercase tracking-wider">
                  {lane.label}
                </text>
                <line x1={lane.x} y1={44} x2={lane.x + 220} y2={44} className="stroke-border" />
              </g>
            ))}
            {chart.edges.map((edge) => {
              const relation = selectedNode
                ? edgeSelectionRelation(edge, selectedNode.id, upstreamIds, downstreamIds)
                : "unrelated";
              return (
                <path
                  key={`${edge.from}->${edge.to}`}
                  d={edge.path}
                  markerEnd={
                    relation === "incoming" || relation === "upstream"
                      ? "url(#lineage-arrow-source)"
                      : relation === "outgoing" || relation === "downstream"
                        ? "url(#lineage-arrow-impact)"
                        : "url(#lineage-arrow-muted)"
                  }
                  className={cn(
                    "fill-none transition-opacity",
                    relation === "outgoing" ? "stroke-primary stroke-[5] opacity-100" : "",
                    relation === "downstream" ? "stroke-primary stroke-[3.5] opacity-90" : "",
                    relation === "incoming" ? "stroke-warning stroke-[5] opacity-100" : "",
                    relation === "upstream" ? "stroke-warning stroke-[3.5] opacity-90" : "",
                    relation === "unrelated" ? "stroke-muted-foreground stroke-2 opacity-10" : "",
                  )}
                  data-lineage-relation={relation}
                />
              );
            })}
            {chart.nodes.map((item) => {
              const node = item.node;
              const selected = selectedNode?.id === node.id;
              const upstream = !selected && upstreamIds.has(node.id);
              const downstream = !selected && downstreamIds.has(node.id);
              const unrelated = Boolean(selectedNode) && !selected && !upstream && !downstream;
              return (
                <g
                  key={node.id}
                  role="button"
                  tabIndex={0}
                  aria-label={`Seleccionar ${node.label || node.id}`}
                  transform={`translate(${item.x} ${item.y})`}
                  className={cn("cursor-pointer outline-none transition-opacity", unrelated ? "opacity-35" : "opacity-100")}
                  onClick={() => setSelectedId(node.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelectedId(node.id);
                    }
                  }}
                >
                  <rect
                    width={220}
                    height={68}
                    rx={8}
                    className={cn(
                      "stroke-border",
                      selected ? "fill-primary/10 stroke-primary stroke-[4]" : "fill-background stroke-[1.5]",
                      downstream ? "fill-primary/10 stroke-primary stroke-[3]" : "",
                      upstream ? "fill-warning/10 stroke-warning stroke-[3]" : "",
                      node.is_stale ? "stroke-warning" : "",
                    )}
                  />
                  <text x={14} y={24} className="fill-foreground text-[12px] font-semibold">
                    {shortText(node.label || node.id, 28)}
                  </text>
                  <text x={14} y={44} className="fill-muted-foreground text-[10px]">
                    {shortText(`${node.cartridge || "sin cartucho"} · ${normaliseLayer(node.type)}`, 34)}
                  </text>
                  <text x={14} y={59} className="fill-muted-foreground text-[10px]">
                    {node.row_count != null ? `${Number(node.row_count).toLocaleString("es")} filas` : formatDate(node.last_refresh) || "sin refresh"}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-[0.9fr_1.1fr]">
        <article className="space-y-3 rounded-lg border bg-card p-4">
          <h2 className="text-base font-semibold">Nodo seleccionado</h2>
          {selectedNode ? (
            <>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h3 className="break-words text-lg font-semibold">{selectedNode.label || selectedNode.id}</h3>
                  <p className="font-mono text-xs text-muted-foreground">{selectedNode.id}</p>
                </div>
                <StatusPill status={selectedNode.is_stale ? "stale" : "fresh"} />
              </div>
              <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
                <DetailItem label="Capa" value={normaliseLayer(selectedNode.type)} />
                <DetailItem label="Cartucho" value={selectedNode.cartridge || "-"} />
                <DetailItem label="Filas" value={selectedNode.row_count ?? "-"} />
                <DetailItem label="Último refresh" value={formatDate(selectedNode.last_refresh) || "-"} />
              </dl>
              {selectedNode.staleness_reason ? (
                <p className="rounded-md border border-warning/30 bg-warning/10 p-3 text-xs text-warning">
                  {selectedNode.staleness_reason}
                </p>
              ) : null}
              {selectedNode.id.startsWith("ds:") ? (
                <Link
                  href={`/viewer?type=dataset&name=${encodeURIComponent(selectedNode.id.slice(3))}`}
                  className="inline-flex min-h-[44px] items-center rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5"
                >
                  Abrir dataset
                </Link>
              ) : null}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">Selecciona un nodo del grafo.</p>
          )}
        </article>
        <article className="space-y-4 rounded-lg border bg-card p-4">
          <h2 className="text-base font-semibold">Dependencias</h2>
          {selectedNode ? (
            <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
              <MetricBox label="Entrantes" value={incoming.length} />
              <MetricBox label="Salientes" value={outgoing.length} />
              <MetricBox label="Origen upstream" value={upstreamIds.size} />
              <MetricBox label="Impacto downstream" value={downstreamIds.size} />
            </div>
          ) : null}
          <DependencyList title="Entrantes" edges={incoming} nodeById={nodeById} direction="from" />
          <DependencyList title="Salientes" edges={outgoing} nodeById={nodeById} direction="to" />
        </article>
      </section>
    </div>
  );
}

function normaliseLayer(value: string | null | undefined): string {
  const layer = String(value || "silver").toLowerCase();
  if (layer === "raw" || layer === "gold") return layer;
  return "silver";
}

function shortText(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, Math.max(0, max - 1))}...` : value;
}

type ReachDirection = "upstream" | "downstream";
type EdgeSelectionRelation = "incoming" | "outgoing" | "upstream" | "downstream" | "unrelated";

function collectReachable(startId: string, edges: LineageEdge[], direction: ReachDirection): Set<string> {
  const visited = new Set<string>();
  const queue = [startId];
  while (queue.length) {
    const current = queue.shift();
    if (!current) continue;
    const nextIds = edges.flatMap((edge) => {
      if (direction === "downstream" && edge.from === current) return [edge.to];
      if (direction === "upstream" && edge.to === current) return [edge.from];
      return [];
    });
    nextIds.forEach((id) => {
      if (id === startId || visited.has(id)) return;
      visited.add(id);
      queue.push(id);
    });
  }
  return visited;
}

function edgeSelectionRelation(
  edge: LineageEdge,
  selectedId: string,
  upstreamIds: Set<string>,
  downstreamIds: Set<string>,
): EdgeSelectionRelation {
  if (edge.from === selectedId) return "outgoing";
  if (edge.to === selectedId) return "incoming";
  if (downstreamIds.has(edge.from) && downstreamIds.has(edge.to)) return "downstream";
  if (upstreamIds.has(edge.from) && upstreamIds.has(edge.to)) return "upstream";
  return "unrelated";
}

function layoutLineageGraph(nodes: LineageNode[], edges: LineageEdge[]) {
  const laneOrder = ["raw", "silver", "gold"];
  const laneX: Record<string, number> = { raw: 36, silver: 336, gold: 636 };
  const rowHeight = 96;
  const top = 70;
  const grouped: Record<string, LineageNode[]> = { raw: [], silver: [], gold: [] };
  nodes.forEach((node) => grouped[normaliseLayer(node.type)].push(node));
  laneOrder.forEach((lane) => grouped[lane].sort((a, b) => String(a.label || a.id).localeCompare(String(b.label || b.id))));
  const positioned = laneOrder.flatMap((lane) => (
    grouped[lane].map((node, index) => ({
      node,
      x: laneX[lane],
      y: top + index * rowHeight,
    }))
  ));
  const pos = new Map(positioned.map((item) => [item.node.id, item]));
  const maxRows = Math.max(1, ...laneOrder.map((lane) => grouped[lane].length));
  return {
    width: 900,
    height: Math.max(560, top + maxRows * rowHeight + 40),
    laneLabels: laneOrder.map((lane) => ({ label: lane, x: laneX[lane] })),
    nodes: positioned,
    edges: edges.flatMap((edge) => {
      const from = pos.get(edge.from);
      const to = pos.get(edge.to);
      if (!from || !to) return [];
      const startX = from.x + 220;
      const startY = from.y + 34;
      const endX = to.x;
      const endY = to.y + 34;
      const mid = Math.max(18, Math.abs(endX - startX) / 2);
      return [{
        ...edge,
        path: `M ${startX} ${startY} C ${startX + mid} ${startY}, ${endX - mid} ${endY}, ${endX - 8} ${endY}`,
      }];
    }),
  };
}

function DependencyList({
  title,
  edges,
  nodeById,
  direction,
}: {
  title: string;
  edges: LineageEdge[];
  nodeById: Map<string, LineageNode>;
  direction: "from" | "to";
}) {
  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {edges.length ? (
        <ul className="space-y-2">
          {edges.map((edge) => {
            const peerId = direction === "from" ? edge.from : edge.to;
            const peer = nodeById.get(peerId);
            return (
              <li key={`${title}:${edge.from}:${edge.to}`} className="rounded-md border bg-background p-3 text-sm">
                <span className="font-medium">{peer?.label || peerId}</span>
                <span className="ml-2 text-xs text-muted-foreground">{normaliseLayer(peer?.type)} {edge.relation || ""}</span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="rounded-md border bg-muted/30 p-3 text-sm text-muted-foreground">Sin dependencias {title.toLowerCase()}.</p>
      )}
    </div>
  );
}

function VaultConnectionsTable({ rows }: { rows: VaultConnection[] }) {
  if (!rows.length) {
    return <EmptyPanel icon={ShieldCheck} title="Sin conexiones" detail="No hay conexiones masked para este cartucho." />;
  }
  return (
    <div className="overflow-x-auto rounded-lg border bg-background">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Conn ID</th>
            <th className="px-3 py-2 font-medium">Base URL</th>
            <th className="px-3 py-2 font-medium">Auth</th>
            <th className="px-3 py-2 font-medium">Actualizado</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.conn_id} className="border-t">
              <td className="px-3 py-2 align-top font-mono text-xs">{row.conn_id}</td>
              <td className="px-3 py-2 align-top text-xs text-muted-foreground">{row.base_url || "-"}</td>
              <td className="px-3 py-2 align-top text-xs">{row.auth_method || "-"}</td>
              <td className="px-3 py-2 align-top text-xs">{formatDate(row.updated_at || row.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function VaultSecretsTable({ rows }: { rows: VaultSecret[] }) {
  if (!rows.length) {
    return <EmptyPanel icon={KeyRound} title="Sin secretos" detail="No hay secretos masked visibles para este scope." />;
  }
  return (
    <div className="overflow-x-auto rounded-lg border bg-background">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Key</th>
            <th className="px-3 py-2 font-medium">Valor</th>
            <th className="px-3 py-2 font-medium">Actualizado</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className="border-t">
              <td className="px-3 py-2 align-top font-mono text-xs">{row.key}</td>
              <td className="px-3 py-2 align-top font-mono text-xs text-muted-foreground">{row.masked_value || row.value || "••••••••"}</td>
              <td className="px-3 py-2 align-top text-xs">{formatDate(row.updated_at || row.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ColumnTable({ columns, sample }: { columns: string[]; sample?: DataRow }) {
  if (!columns.length) {
    return <EmptyPanel icon={Network} title="Sin columnas" detail="El preview no devolvió columnas." />;
  }
  return (
    <div className="overflow-x-auto rounded-lg border bg-background">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">#</th>
            <th className="px-3 py-2 font-medium">Columna</th>
            <th className="px-3 py-2 font-medium">Tipo</th>
            <th className="px-3 py-2 font-medium">Ejemplo</th>
          </tr>
        </thead>
        <tbody>
          {columns.map((column, index) => {
            const value = sample?.[column];
            return (
              <tr key={column} className="border-t">
                <td className="px-3 py-2 align-top text-xs text-muted-foreground">{index + 1}</td>
                <td className="px-3 py-2 align-top font-mono text-xs">{column}</td>
                <td className="px-3 py-2 align-top text-xs">{guessType(value)}</td>
                <td className="px-3 py-2 align-top text-xs text-muted-foreground">{displayValue(value)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PreviewTable({ rows }: { rows: DataRow[] }) {
  const columns = columnsFromRows(rows);
  if (!rows.length || !columns.length) {
    return <EmptyPanel icon={Table2} title="Sin preview" detail="No hay filas disponibles para mostrar." />;
  }
  return (
    <div className="overflow-x-auto rounded-lg border bg-background">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
          <tr>{columns.map((column) => <th key={column} className="px-3 py-2 font-medium">{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="border-t">
              {columns.map((column) => (
                <td key={column} className="max-w-64 truncate px-3 py-2 align-top text-xs text-muted-foreground">
                  {displayValue(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MetricBox({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-md border bg-background p-3">
      <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="mt-1 break-words text-sm font-semibold">{value}</div>
    </div>
  );
}

function LayerPill({ layer }: { layer?: string | null }) {
  const value = (layer || "silver").toLowerCase();
  return (
    <span className="inline-flex items-center rounded-full border bg-background px-2 py-0.5 text-[11px] font-medium uppercase">
      {value}
    </span>
  );
}

function flattenSemantic(payload: SemanticPayload | undefined): SemanticEntity[] {
  if (!payload?.entities) return [];
  if (Array.isArray(payload.entities)) return payload.entities;
  return Object.values(payload.entities).flat();
}

function columnsFromRows(rows: DataRow[]): string[] {
  const first = rows[0];
  return first ? Object.keys(first) : [];
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string") return value.length > 80 ? `${value.slice(0, 77)}...` : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function guessType(value: unknown): string {
  if (value === null || value === undefined) return "UNKNOWN";
  if (typeof value === "boolean") return "BOOLEAN";
  if (typeof value === "number") return Number.isInteger(value) ? "INTEGER" : "DOUBLE";
  if (typeof value === "string") {
    if (/^\d{4}-\d{2}-\d{2}T/.test(value)) return "TIMESTAMP";
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return "DATE";
    if (/^\d+$/.test(value)) return "VARCHAR(NUM)";
    return "VARCHAR";
  }
  return "OBJECT";
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
