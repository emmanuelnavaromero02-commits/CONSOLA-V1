"use client";

import { useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { GitBranch, Layers3, RefreshCw, Search, TriangleAlert } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { getDataLineage } from "@/lib/data/client";
import type { LineageEdge, LineageNode } from "@/lib/data/types";
import { cn } from "@/lib/utils";

const CARTRIDGES = ["replicon", "hubspot", "sap_hcm", "sap_s4hana", "sap_successfactors"] as const;
const TYPE_ORDER = ["raw", "bronze", "silver", "gold", "master"] as const;

export default function DataLineagePage() {
  const [cartridge, setCartridge] = useState("");
  const [search, setSearch] = useState("");

  const lineage = useQuery({
    queryKey: ["data", "lineage", cartridge],
    queryFn: () => getDataLineage(cartridge || undefined),
  });

  const filteredNodes = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const nodes = lineage.data?.nodes ?? [];
    if (!needle) return nodes;
    return nodes.filter((node) => (
      node.id.toLowerCase().includes(needle)
      || (node.label ?? "").toLowerCase().includes(needle)
      || (node.type ?? "").toLowerCase().includes(needle)
      || (node.cartridge ?? "").toLowerCase().includes(needle)
    ));
  }, [lineage.data?.nodes, search]);

  const visibleIds = useMemo(() => new Set(filteredNodes.map((node) => node.id)), [filteredNodes]);
  const filteredEdges = useMemo(() => (
    (lineage.data?.edges ?? []).filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to))
  ), [lineage.data?.edges, visibleIds]);

  const metrics = {
    nodes: filteredNodes.length,
    edges: filteredEdges.length,
    stale: filteredNodes.filter((node) => node.is_stale).length,
  };

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-6">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Mapa de datos</h1>
          <p className="text-sm text-muted-foreground">
            De dónde salen tus datos y cómo se transforman hasta llegar a los reportes.
          </p>
        </div>
        <button
          type="button"
          onClick={() => lineage.refetch()}
          className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RefreshCw aria-hidden className={cn("h-4 w-4", lineage.isFetching && "animate-spin")} />
          Refrescar
        </button>
      </header>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-3" aria-label="Resumen del linaje">
        <MetricCard icon={Layers3} label="Nodos" value={metrics.nodes} />
        <MetricCard icon={GitBranch} label="Relaciones" value={metrics.edges} />
        <MetricCard icon={TriangleAlert} label="Atrasados" value={metrics.stale} />
      </section>

      <section className="rounded-lg border bg-card p-4 shadow-sm">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-[220px_minmax(260px,1fr)]">
          <label className="space-y-1 text-sm">
            <span className="text-xs font-medium uppercase text-muted-foreground">Cartucho</span>
            <select
              value={cartridge}
              onChange={(event) => setCartridge(event.target.value)}
              className="min-h-[44px] w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value="">Todos</option>
              {CARTRIDGES.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-xs font-medium uppercase text-muted-foreground">Buscar</span>
            <div className="relative">
              <Search aria-hidden className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                className="min-h-[44px] w-full rounded-md border bg-background pl-9 pr-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                placeholder="Dataset, capa o cartucho"
              />
            </div>
          </label>
        </div>
      </section>

      {lineage.isError ? (
        <ErrorPanel message="No se pudo cargar el linaje." onRetry={() => lineage.refetch()} />
      ) : lineage.isLoading ? (
        <SkeletonBoard />
      ) : (
        <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)]">
          <LineageBoard nodes={filteredNodes} edges={filteredEdges} />
          <EdgesPanel edges={filteredEdges} nodes={filteredNodes} />
        </section>
      )}
    </main>
  );
}

function LineageBoard({ nodes, edges }: { nodes: LineageNode[]; edges: LineageEdge[] }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const selectedNode = selectedId ? nodes.find((node) => node.id === selectedId) ?? nodes[0] : nodes[0];
  const upstreamIds = selectedNode ? collectReachable(selectedNode.id, edges, "upstream") : new Set<string>();
  const downstreamIds = selectedNode ? collectReachable(selectedNode.id, edges, "downstream") : new Set<string>();
  const chart = useMemo(() => layoutLineageGraph(nodes, edges), [nodes, edges]);

  return (
    <div className="overflow-hidden rounded-lg border bg-card shadow-sm">
      <header className="border-b px-4 py-3">
        <h2 className="text-base font-semibold">Flujo por capa</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          RAW → BRONZE → SILVER con flechas direccionales entre dependencias.
        </p>
      </header>
      {nodes.length === 0 ? (
        <EmptyState label="Sin nodos visibles." />
      ) : (
        <div className="space-y-4 p-4">
          <div className="overflow-auto rounded-lg border bg-background">
            <svg
              className="min-h-[520px] min-w-[860px] w-full"
              viewBox={`0 0 ${chart.width} ${chart.height}`}
              role="img"
              aria-label="Grafo de linaje con flechas RAW, BRONZE y SILVER"
            >
              <defs>
                <marker
                  id="data-lineage-arrow-muted"
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
                  id="data-lineage-arrow-impact"
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
                  id="data-lineage-arrow-source"
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="8"
                  markerHeight="8"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 0 L 10 5 L 0 10 z" className="fill-amber-500" />
                </marker>
              </defs>

              {chart.laneLabels.map((lane) => (
                <g key={lane.label}>
                  <rect
                    x={lane.x - 14}
                    y={16}
                    width={244}
                    height={chart.height - 32}
                    rx={10}
                    className="fill-muted/20 stroke-border"
                  />
                  <text x={lane.x} y={42} className="fill-muted-foreground text-[12px] font-semibold uppercase">
                    {lane.label}
                  </text>
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
                        ? "url(#data-lineage-arrow-source)"
                        : relation === "outgoing" || relation === "downstream"
                          ? "url(#data-lineage-arrow-impact)"
                          : "url(#data-lineage-arrow-muted)"
                    }
                    className={cn(
                      "fill-none transition-opacity",
                      relation === "outgoing" ? "stroke-primary stroke-[5] opacity-100" : "",
                      relation === "downstream" ? "stroke-primary stroke-[3.5] opacity-90" : "",
                      relation === "incoming" ? "stroke-amber-500 stroke-[5] opacity-100" : "",
                      relation === "upstream" ? "stroke-amber-500 stroke-[3.5] opacity-90" : "",
                      relation === "unrelated" ? "stroke-muted-foreground stroke-2 opacity-25" : "",
                    )}
                  />
                );
              })}

              {chart.nodes.map((item) => {
                const node = item.node;
                const selected = selectedNode?.id === node.id;
                const focused = focusedId === node.id;
                const upstream = !selected && upstreamIds.has(node.id);
                const downstream = !selected && downstreamIds.has(node.id);
                const unrelated = Boolean(selectedNode) && !selected && !upstream && !downstream;
                return (
                  <g
                    key={node.id}
                    role="button"
                    tabIndex={0}
                    aria-pressed={selected}
                    aria-label={`Seleccionar ${node.label || node.id}`}
                    transform={`translate(${item.x} ${item.y})`}
                    className={cn("cursor-pointer transition-opacity", unrelated ? "opacity-50" : "opacity-100")}
                    onClick={() => setSelectedId(node.id)}
                    onFocus={() => setFocusedId(node.id)}
                    onBlur={() => setFocusedId((current) => current === node.id ? null : current)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setSelectedId(node.id);
                      }
                    }}
                  >
                    <rect
                      width={220}
                      height={76}
                      rx={8}
                      className={cn(
                        "stroke-border",
                        selected ? "fill-primary/10 stroke-primary stroke-[4]" : "fill-card stroke-[1.5]",
                        focused ? "stroke-primary stroke-[4]" : "",
                        downstream ? "fill-primary/10 stroke-primary stroke-[3]" : "",
                        upstream ? "fill-amber-500/10 stroke-amber-500 stroke-[3]" : "",
                        node.is_stale ? "stroke-destructive" : "",
                      )}
                    />
                    <text x={14} y={24} className="fill-foreground text-[12px] font-semibold">
                      {shortText(node.label || node.id, 28)}
                    </text>
                    <text x={14} y={44} className="fill-muted-foreground text-[10px]">
                      {shortText(`${node.cartridge || "sin cartucho"} · ${normaliseLayer(node)}`, 34)}
                    </text>
                    <text x={14} y={61} className="fill-muted-foreground text-[10px]">
                      {node.row_count != null ? `${formatNumber(node.row_count)} filas` : node.last_refresh || "sin refresh"}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>

          {selectedNode ? (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-[minmax(0,1fr)_220px]">
              <NodeCard node={selectedNode} />
              <div className="grid grid-cols-2 gap-2 text-xs">
                <MiniMetric label="Entrantes" value={edges.filter((edge) => edge.to === selectedNode.id).length} />
                <MiniMetric label="Salientes" value={edges.filter((edge) => edge.from === selectedNode.id).length} />
                <MiniMetric label="Upstream" value={upstreamIds.size} />
                <MiniMetric label="Downstream" value={downstreamIds.size} />
              </div>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
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

function normaliseLayer(node: LineageNode): string {
  const value = `${node.type || ""} ${node.id || ""} ${node.label || ""}`.toLowerCase();
  if (value.includes("raw")) return "raw";
  if (value.includes("bronze")) return "bronze";
  if (value.includes("silver")) return "silver";
  if (value.includes("gold")) return "gold";
  if (value.includes("master")) return "master";
  return "unknown";
}

function shortText(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, Math.max(0, max - 1))}...` : value;
}

function layoutLineageGraph(nodes: LineageNode[], edges: LineageEdge[]) {
  const lanes = [...TYPE_ORDER, "unknown"];
  const laneWidth = 280;
  const nodeWidth = 220;
  const rowHeight = 104;
  const left = 36;
  const top = 72;
  const grouped = new Map<string, LineageNode[]>();

  for (const node of nodes) {
    const layer = normaliseLayer(node);
    grouped.set(layer, [...(grouped.get(layer) ?? []), node]);
  }
  lanes.forEach((lane) => {
    grouped.set(
      lane,
      [...(grouped.get(lane) ?? [])].sort((a, b) => String(a.label || a.id).localeCompare(String(b.label || b.id))),
    );
  });

  const positioned = lanes.flatMap((lane, laneIndex) => (
    (grouped.get(lane) ?? []).map((node, index) => ({
      node,
      x: left + laneIndex * laneWidth,
      y: top + index * rowHeight,
    }))
  ));
  const pos = new Map(positioned.map((item) => [item.node.id, item]));
  const maxRows = Math.max(1, ...lanes.map((lane) => grouped.get(lane)?.length ?? 0));
  const width = left + lanes.length * laneWidth + 20;

  return {
    width,
    height: Math.max(560, top + maxRows * rowHeight + 52),
    laneLabels: lanes.map((lane, laneIndex) => ({ label: lane, x: left + laneIndex * laneWidth })),
    nodes: positioned,
    edges: edges.flatMap((edge) => {
      const from = pos.get(edge.from);
      const to = pos.get(edge.to);
      if (!from || !to) return [];
      const startX = from.x + nodeWidth;
      const startY = from.y + 38;
      const endX = to.x;
      const endY = to.y + 38;
      const sameLane = Math.abs(endX - startX) < 24;
      const control = sameLane ? 80 : Math.max(36, Math.abs(endX - startX) / 2);
      const path = sameLane
        ? `M ${startX - nodeWidth / 2} ${startY + 38} C ${startX - nodeWidth / 2 + control} ${startY + 76}, ${endX + nodeWidth / 2 - control} ${endY - 38}, ${endX + nodeWidth / 2} ${endY - 2}`
        : `M ${startX} ${startY} C ${startX + control} ${startY}, ${endX - control} ${endY}, ${endX - 8} ${endY}`;
      return [{ ...edge, path }];
    }),
  };
}

function EdgesPanel({ edges, nodes }: { edges: LineageEdge[]; nodes: LineageNode[] }) {
  const labels = useMemo(() => new Map(nodes.map((node) => [node.id, node.label || node.id])), [nodes]);
  return (
    <div className="rounded-lg border bg-card shadow-sm">
      <header className="border-b px-4 py-3">
        <h2 className="text-base font-semibold">Relaciones</h2>
      </header>
      {edges.length === 0 ? (
        <EmptyState label="Sin relaciones visibles." />
      ) : (
        <div className="max-h-[720px] divide-y overflow-auto">
          {edges.map((edge, index) => (
            <div key={`${edge.from}:${edge.to}:${index}`} className="px-4 py-3 text-sm">
              <div className="font-medium">{labels.get(edge.from) ?? edge.from}</div>
              <div className="my-1 text-xs text-muted-foreground">→</div>
              <div className="font-medium">{labels.get(edge.to) ?? edge.to}</div>
              {edge.relation ? <div className="mt-1 text-xs text-muted-foreground">{edge.relation}</div> : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function NodeCard({ node }: { node: LineageNode }) {
  return (
    <article className="rounded-md border bg-card px-3 py-2 text-sm shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate font-medium">{node.label || node.id}</h3>
          <p className="truncate text-xs text-muted-foreground">{node.cartridge || "sin cartucho"}</p>
        </div>
        {node.is_stale ? <span className="rounded-md bg-destructive/10 px-2 py-0.5 text-xs text-destructive">atrasado</span> : null}
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-2 text-xs">
        <div>
          <dt className="text-muted-foreground">Filas</dt>
          <dd className="font-medium">{formatNumber(node.row_count)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Refresh</dt>
          <dd className="truncate font-medium">{node.last_refresh || "n/a"}</dd>
        </div>
      </dl>
      {node.staleness_reason ? <p className="mt-2 text-xs text-destructive">{node.staleness_reason}</p> : null}
    </article>
  );
}

function MiniMetric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-md border bg-background p-3">
      <p className="text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
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

function ErrorPanel({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <span>{message}</span>
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex min-h-[40px] items-center justify-center rounded-md border bg-background px-3 text-sm font-medium text-foreground hover:bg-accent/5"
        >
          Reintentar
        </button>
      </div>
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return <div className="px-4 py-10 text-center text-sm text-muted-foreground">{label}</div>;
}

function SkeletonBoard() {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)]">
      <div className="rounded-lg border bg-card p-4 shadow-sm">
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => <div key={index} className="h-32 rounded-md bg-muted" />)}
        </div>
      </div>
      <div className="rounded-lg border bg-card p-4 shadow-sm">
        {Array.from({ length: 5 }).map((_, index) => <div key={index} className="mb-3 h-12 rounded-md bg-muted" />)}
      </div>
    </div>
  );
}

function formatNumber(value: number | null | undefined) {
  if (value === null || value === undefined) return "n/a";
  return new Intl.NumberFormat("es-ES").format(value);
}
