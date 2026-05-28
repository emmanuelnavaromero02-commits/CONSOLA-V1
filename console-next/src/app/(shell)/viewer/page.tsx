"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState, type ReactNode } from "react";
import { ArrowLeft, Database, Droplets, FileText, GitBranch, Layers3, ListChecks, RefreshCcw } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { JobTable } from "@/components/monitor/JobTable";
import { PipelineTable } from "@/components/monitor/PipelineTable";
import { StatusPill } from "@/components/monitor/StatusPill";
import { cn } from "@/lib/utils";
import { useFreshness, useJob, useJobLogs, useJobs, useLineage, usePipeline, useSemantic } from "@/lib/monitor/hooks";
import type {
  FreshnessEntity,
  JobLogLine,
  JobRun,
  LineageEdge,
  LineageNode,
  SemanticEntity,
  SemanticPayload,
} from "@/lib/monitor/types";

const DEFAULT_CARTRIDGE = "replicon";

type ViewerType = "jobs" | "job" | "pipeline" | "watermarks" | "semantic" | "lineage";

const VIEWER_LINKS: Array<{ type: ViewerType; label: string; icon: LucideIcon }> = [
  { type: "jobs", label: "Jobs", icon: ListChecks },
  { type: "pipeline", label: "Pipeline", icon: Layers3 },
  { type: "watermarks", label: "Watermarks", icon: Droplets },
  { type: "semantic", label: "Semantic", icon: Database },
  { type: "lineage", label: "Lineage", icon: GitBranch },
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
  if (type === "lineage") {
    return <LineageViewer cartridge={cartridge} />;
  }
  return <JobsViewer />;
}

function normaliseType(value: string | null): ViewerType {
  if (value === "job" || value === "logs") return "job";
  if (value === "pipeline") return "pipeline";
  if (value === "watermarks" || value === "freshness") return "watermarks";
  if (value === "semantic" || value === "semantic-layer") return "semantic";
  if (value === "lineage" || value === "graph") return "lineage";
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

const LINEAGE_LAYERS = ["all", "raw", "bronze", "silver", "gold", "semantic"] as const;
type LineageLayerFilter = (typeof LINEAGE_LAYERS)[number];

interface NodePosition {
  x: number;
  y: number;
}

function LineageViewer({ cartridge }: { cartridge: string }) {
  const lineage = useLineage(cartridge);
  const [layer, setLayer] = useState<LineageLayerFilter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const nodes = useMemo(() => lineage.data?.nodes ?? [], [lineage.data?.nodes]);
  const edges = useMemo(() => lineage.data?.edges ?? [], [lineage.data?.edges]);
  const visibleNodes = useMemo(
    () => nodes.filter((node) => layer === "all" || normalizeLayer(node) === layer),
    [layer, nodes],
  );
  const visibleNodeIds = useMemo(() => new Set(visibleNodes.map((node) => node.id)), [visibleNodes]);
  const visibleEdges = useMemo(
    () => edges.filter((edge) => visibleNodeIds.has(edge.from) && visibleNodeIds.has(edge.to)),
    [edges, visibleNodeIds],
  );
  const graph = useMemo(() => layoutLineage(visibleNodes), [visibleNodes]);
  const selectedNode = useMemo(
    () => visibleNodes.find((node) => node.id === selectedId) ?? visibleNodes[0] ?? null,
    [selectedId, visibleNodes],
  );
  const relatedEdgeKeys = useMemo(() => {
    const id = selectedNode?.id;
    if (!id) return new Set<string>();
    return new Set(
      visibleEdges
        .filter((edge) => edge.from === id || edge.to === id)
        .map((edge) => edgeKey(edge)),
    );
  }, [selectedNode, visibleEdges]);

  return (
    <ViewerShell
      title="Lineage"
      subtitle={`Cartucho ${cartridge}: grafo de dependencias raw → silver → gold con selección y detalle.`}
      actions={<RefreshButton onClick={() => lineage.refetch()} />}
    >
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section className="space-y-4 rounded-lg border bg-card p-4" aria-label="Grafo de linaje">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
              <Metric label="Nodos" value={visibleNodes.length} />
              <Metric label="Flechas" value={visibleEdges.length} />
              <Metric label="Gold" value={visibleNodes.filter((node) => normalizeLayer(node) === "gold").length} />
              <Metric label="Stale" value={visibleNodes.filter((node) => Boolean(node.is_stale ?? node.stale)).length} />
            </div>
            <div className="flex flex-wrap gap-1" role="tablist" aria-label="Filtro de capa">
              {LINEAGE_LAYERS.map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => setLayer(item)}
                  aria-pressed={layer === item}
                  className={cn(
                    "min-h-[36px] rounded-md border px-3 text-xs font-medium capitalize transition-colors",
                    layer === item
                      ? "border-primary bg-primary text-primary-foreground"
                      : "bg-background hover:bg-accent/5",
                  )}
                >
                  {item === "all" ? "Todas" : item}
                </button>
              ))}
            </div>
          </div>

          {lineage.isError ? (
            <ErrorPanel message="No se pudo cargar el linaje." onRetry={() => lineage.refetch()} />
          ) : lineage.isLoading ? (
            <SkeletonRows rows={8} />
          ) : !visibleNodes.length ? (
            <EmptyPanel
              icon={GitBranch}
              title="Sin nodos"
              detail="El backend no devolvió datasets para este filtro de linaje."
            />
          ) : (
            <div className="overflow-auto rounded-lg border bg-background">
              <svg
                role="img"
                aria-label="Grafo de linaje de datos"
                viewBox={`0 0 ${graph.width} ${graph.height}`}
                className="block min-h-[520px] w-full min-w-[760px]"
              >
                <defs>
                  <marker id="lineage-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                    <path d="M 0 0 L 10 5 L 0 10 z" className="fill-muted-foreground" />
                  </marker>
                  <marker id="lineage-arrow-active" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                    <path d="M 0 0 L 10 5 L 0 10 z" className="fill-primary" />
                  </marker>
                </defs>
                {visibleEdges.map((edge) => {
                  const from = graph.positions.get(edge.from);
                  const to = graph.positions.get(edge.to);
                  if (!from || !to) return null;
                  const related = relatedEdgeKeys.has(edgeKey(edge));
                  const x1 = from.x + 220;
                  const y1 = from.y + 38;
                  const x2 = to.x;
                  const y2 = to.y + 38;
                  const mid = Math.max(28, Math.abs(x2 - x1) / 2);
                  return (
                    <path
                      key={edgeKey(edge)}
                      d={`M ${x1} ${y1} C ${x1 + mid} ${y1}, ${x2 - mid} ${y2}, ${x2} ${y2}`}
                      className={cn(
                        "fill-none stroke-muted-foreground/45 transition-colors",
                        related && "stroke-primary",
                      )}
                      strokeWidth={related ? 3 : 2}
                      markerEnd={related ? "url(#lineage-arrow-active)" : "url(#lineage-arrow)"}
                    />
                  );
                })}
                {visibleNodes.map((node) => {
                  const position = graph.positions.get(node.id);
                  if (!position) return null;
                  const active = selectedNode?.id === node.id;
                  const stale = Boolean(node.is_stale ?? node.stale);
                  return (
                    <g
                      key={node.id}
                      role="button"
                      tabIndex={0}
                      aria-label={nodeLabel(node)}
                      onClick={() => setSelectedId(node.id)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          setSelectedId(node.id);
                        }
                      }}
                      transform={`translate(${position.x}, ${position.y})`}
                      className="cursor-pointer outline-none"
                    >
                      <rect
                        width="220"
                        height="76"
                        rx="8"
                        className={cn(
                          "fill-card stroke-border transition-colors",
                          active && "stroke-primary",
                          stale && !active && "stroke-warning",
                        )}
                        strokeWidth={active ? 3 : stale ? 2.5 : 1.5}
                      />
                      <text x="14" y="24" className="fill-foreground font-mono text-[12px] font-semibold">
                        {truncate(nodeLabel(node), 24)}
                      </text>
                      <text x="14" y="45" className="fill-muted-foreground text-[11px] font-medium">
                        {normalizeLayer(node)} · {node.cartridge || cartridge}
                      </text>
                      <text x="14" y="64" className="fill-muted-foreground text-[10px]">
                        {nodeRows(node)} rows · {formatDate(node.last_refresh || node.updated_at)}
                      </text>
                    </g>
                  );
                })}
              </svg>
            </div>
          )}
        </section>

        <LineageDetail
          cartridge={cartridge}
          node={selectedNode}
          nodes={visibleNodes}
          edges={visibleEdges}
          onSelect={setSelectedId}
        />
      </div>
    </ViewerShell>
  );
}

function LineageDetail({
  cartridge,
  node,
  nodes,
  edges,
  onSelect,
}: {
  cartridge: string;
  node: LineageNode | null;
  nodes: LineageNode[];
  edges: LineageEdge[];
  onSelect: (id: string) => void;
}) {
  const byId = useMemo(() => new Map(nodes.map((item) => [item.id, item])), [nodes]);
  const incoming = useMemo(() => edges.filter((edge) => edge.to === node?.id), [edges, node]);
  const outgoing = useMemo(() => edges.filter((edge) => edge.from === node?.id), [edges, node]);

  if (!node) {
    return (
      <section className="rounded-lg border bg-card p-4" aria-label="Detalle de linaje">
        <EmptyPanel icon={GitBranch} title="Sin selección" detail="Selecciona un nodo para ver dependencias y metadata." />
      </section>
    );
  }

  return (
    <aside className="space-y-4 rounded-lg border bg-card p-4" aria-label="Detalle de linaje">
      <div className="space-y-1">
        <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Nodo seleccionado</p>
        <h2 className="break-words text-xl font-semibold tracking-tight">{nodeLabel(node)}</h2>
        <p className="break-all font-mono text-xs text-muted-foreground">{node.id}</p>
      </div>
      <dl className="grid grid-cols-2 gap-2 text-sm">
        <DetailItem label="Capa" value={<span className="capitalize">{normalizeLayer(node)}</span>} />
        <DetailItem label="Cartucho" value={node.cartridge || cartridge} />
        <DetailItem label="Filas" value={nodeRows(node).toLocaleString()} />
        <DetailItem label="Estado" value={<StatusPill status={node.status || (node.is_stale || node.stale ? "stale" : "fresh")} />} />
        <DetailItem label="Refresh" value={formatDate(node.last_refresh || node.updated_at)} />
        <DetailItem label="Stale" value={node.is_stale || node.stale ? "Sí" : "No"} />
      </dl>
      {node.staleness_reason ? (
        <div className="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning-foreground">
          {node.staleness_reason}
        </div>
      ) : null}

      <DependencyList
        title="Depende de"
        edges={incoming}
        direction="in"
        byId={byId}
        onSelect={onSelect}
      />
      <DependencyList
        title="Usado por"
        edges={outgoing}
        direction="out"
        byId={byId}
        onSelect={onSelect}
      />

      <div className="flex flex-wrap gap-2">
        <Link
          href={`/viewer?type=semantic&cartridge=${encodeURIComponent(cartridge)}`}
          className="inline-flex min-h-[40px] items-center justify-center rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5"
        >
          Ver semantic
        </Link>
        <Link
          href={`/viewer?type=pipeline&cartridge=${encodeURIComponent(cartridge)}`}
          className="inline-flex min-h-[40px] items-center justify-center rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5"
        >
          Ver pipeline
        </Link>
      </div>
    </aside>
  );
}

function DependencyList({
  title,
  edges,
  direction,
  byId,
  onSelect,
}: {
  title: string;
  edges: LineageEdge[];
  direction: "in" | "out";
  byId: Map<string, LineageNode>;
  onSelect: (id: string) => void;
}) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {!edges.length ? (
        <p className="rounded-md border bg-background p-3 text-xs text-muted-foreground">Sin dependencias visibles.</p>
      ) : (
        <div className="space-y-2">
          {edges.map((edge) => {
            const id = direction === "in" ? edge.from : edge.to;
            const other = byId.get(id);
            return (
              <button
                key={`${direction}:${edgeKey(edge)}`}
                type="button"
                onClick={() => onSelect(id)}
                className="flex min-h-[44px] w-full items-center justify-between gap-3 rounded-md border bg-background px-3 py-2 text-left text-xs hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="min-w-0">
                  <span className="block truncate font-medium">{other ? nodeLabel(other) : id}</span>
                  <span className="block truncate font-mono text-muted-foreground">{id}</span>
                </span>
                <span className="shrink-0 rounded-md bg-muted px-2 py-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                  {other ? normalizeLayer(other) : "node"}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border bg-background px-3 py-2">
      <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="text-lg font-semibold">{value}</div>
    </div>
  );
}

function layoutLineage(nodes: LineageNode[]): {
  positions: Map<string, NodePosition>;
  width: number;
  height: number;
} {
  const groups = new Map<string, LineageNode[]>();
  for (const node of nodes) {
    const key = normalizeLayer(node);
    groups.set(key, [...(groups.get(key) ?? []), node]);
  }

  const layers = [...groups.keys()].sort((a, b) => layerOrder(a) - layerOrder(b) || a.localeCompare(b));
  const positions = new Map<string, NodePosition>();
  const xGap = 290;
  const yGap = 108;
  const left = 28;
  const top = 28;
  let maxRows = 1;

  layers.forEach((layer, layerIndex) => {
    const group = [...(groups.get(layer) ?? [])].sort((a, b) => nodeLabel(a).localeCompare(nodeLabel(b)));
    maxRows = Math.max(maxRows, group.length);
    group.forEach((node, rowIndex) => {
      positions.set(node.id, { x: left + layerIndex * xGap, y: top + rowIndex * yGap });
    });
  });

  return {
    positions,
    width: Math.max(760, left * 2 + Math.max(layers.length, 1) * xGap),
    height: Math.max(520, top * 2 + maxRows * yGap),
  };
}

function normalizeLayer(node: LineageNode): string {
  const raw = String(node.layer || node.type || "").toLowerCase();
  if (raw.includes("raw")) return "raw";
  if (raw.includes("bronze")) return "bronze";
  if (raw.includes("silver")) return "silver";
  if (raw.includes("gold")) return "gold";
  if (raw.includes("semantic")) return "semantic";
  return raw || "other";
}

function layerOrder(layer: string): number {
  if (layer === "raw") return 0;
  if (layer === "bronze") return 1;
  if (layer === "silver") return 2;
  if (layer === "gold") return 3;
  if (layer === "semantic") return 4;
  return 5;
}

function nodeLabel(node: LineageNode): string {
  return node.label || node.name || node.id;
}

function nodeRows(node: LineageNode): number {
  const value = node.row_count ?? node.rows ?? 0;
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function edgeKey(edge: LineageEdge): string {
  return `${edge.from}->${edge.to}`;
}

function truncate(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, Math.max(0, max - 1))}…` : value;
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
