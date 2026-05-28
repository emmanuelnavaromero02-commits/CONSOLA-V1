"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState, type ReactNode } from "react";
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

const DEFAULT_CARTRIDGE = "replicon";

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
  const cartridge = params.get("cartridge") || DEFAULT_CARTRIDGE;
  const jobId = params.get("id") || params.get("job_id");
  const datasetName = params.get("name") || params.get("dataset") || params.get("id");
  const source = params.get("source");
  const scope = params.get("scope") || cartridge;

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
    return <LineageViewer cartridge={params.get("cartridge") || ""} />;
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
          href={viewerHref(type)}
          className="inline-flex min-h-[44px] items-center gap-2 rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Icon aria-hidden className="h-4 w-4" />
          {label}
        </Link>
      ))}
    </nav>
  );
}

function viewerHref(type: ViewerType): string {
  if (type === "jobs") return "/viewer?type=jobs";
  if (type === "schema") return "/viewer?type=schema";
  if (type === "datasets") return "/viewer?type=datasets";
  if (type === "lineage") return "/viewer?type=lineage";
  if (type === "vault") return `/viewer?type=vault&cartridge=${DEFAULT_CARTRIDGE}&scope=${DEFAULT_CARTRIDGE}`;
  return `/viewer?type=${type}&cartridge=${DEFAULT_CARTRIDGE}`;
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
  const rows = payload?.preview?.rows ?? payload?.preview?.result ?? [];
  const columns = payload?.preview?.columns ?? columnsFromRows(rows);

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
  if (!nodes.length) {
    return <EmptyPanel icon={GitBranch} title="Sin lineage" detail="No hay nodos visibles para el filtro actual." />;
  }
  const incomingCounts = new Map<string, number>();
  const outgoingCounts = new Map<string, number>();
  edges.forEach((edge) => {
    incomingCounts.set(edge.to, (incomingCounts.get(edge.to) ?? 0) + 1);
    outgoingCounts.set(edge.from, (outgoingCounts.get(edge.from) ?? 0) + 1);
  });
  const groups = ["raw", "silver", "gold"].map((type) => ({
    type,
    rows: nodes.filter((node) => (node.type || "silver") === type),
  }));
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
      {groups.map((group) => (
        <section key={group.type} className="space-y-3 rounded-lg border bg-card p-4">
          <h2 className="text-base font-semibold uppercase tracking-wider">{group.type}</h2>
          {group.rows.length ? group.rows.map((node) => (
            <article key={node.id} className="space-y-2 rounded-md border bg-background p-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <h3 className="break-words text-sm font-medium">{node.label || node.id}</h3>
                  <p className="text-xs text-muted-foreground">{node.cartridge || "sin cartucho"}</p>
                </div>
                <StatusPill status={node.is_stale ? "stale" : "fresh"} />
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                <span>Entrada: {incomingCounts.get(node.id) ?? 0}</span>
                <span>Salida: {outgoingCounts.get(node.id) ?? 0}</span>
                <span>Filas: {node.row_count ?? "-"}</span>
                <span>{formatDate(node.last_refresh)}</span>
              </div>
              {node.staleness_reason ? <p className="text-xs text-warning">{node.staleness_reason}</p> : null}
            </article>
          )) : (
            <p className="rounded-md border bg-muted/30 p-3 text-sm text-muted-foreground">Sin nodos.</p>
          )}
        </section>
      ))}
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
