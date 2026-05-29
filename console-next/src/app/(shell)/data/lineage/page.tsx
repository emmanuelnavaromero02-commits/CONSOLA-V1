"use client";

import { useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { GitBranch, Layers3, RefreshCw, Search, TriangleAlert } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { getDataLineage } from "@/lib/data/client";
import type { LineageEdge, LineageNode } from "@/lib/data/types";
import { cn } from "@/lib/utils";

const CARTRIDGES = ["replicon", "sap_hcm", "sap_s4hana", "sap_successfactors"] as const;
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
          <h1 className="text-2xl font-semibold tracking-tight">Linaje de datos</h1>
          <p className="text-sm text-muted-foreground">
            Grafo operativo de fuentes, datasets derivados y relaciones de transformación.
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
        <MetricCard icon={TriangleAlert} label="Stale" value={metrics.stale} />
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
          <LineageBoard nodes={filteredNodes} />
          <EdgesPanel edges={filteredEdges} nodes={filteredNodes} />
        </section>
      )}
    </main>
  );
}

function LineageBoard({ nodes }: { nodes: LineageNode[] }) {
  const grouped = useMemo(() => {
    const groups = new Map<string, LineageNode[]>();
    for (const node of nodes) {
      const type = (node.type || "unknown").toLowerCase();
      const key = TYPE_ORDER.includes(type as (typeof TYPE_ORDER)[number]) ? type : "unknown";
      groups.set(key, [...(groups.get(key) ?? []), node]);
    }
    return [...TYPE_ORDER, "unknown"].map((type) => ({ type, nodes: groups.get(type) ?? [] }));
  }, [nodes]);

  return (
    <div className="rounded-lg border bg-card shadow-sm">
      <header className="border-b px-4 py-3">
        <h2 className="text-base font-semibold">Flujo por capa</h2>
      </header>
      {nodes.length === 0 ? (
        <EmptyState label="Sin nodos visibles." />
      ) : (
        <div className="grid grid-cols-1 gap-3 p-4 md:grid-cols-2 2xl:grid-cols-3">
          {grouped.map((group) => (
            <div key={group.type} className="rounded-lg border bg-background">
              <div className="border-b px-3 py-2 text-xs font-semibold uppercase text-muted-foreground">{group.type}</div>
              <div className="space-y-2 p-3">
                {group.nodes.length === 0 ? (
                  <div className="rounded-md border border-dashed px-3 py-4 text-center text-xs text-muted-foreground">Vacío</div>
                ) : group.nodes.map((node) => <NodeCard key={node.id} node={node} />)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
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
        {node.is_stale ? <span className="rounded-md bg-destructive/10 px-2 py-0.5 text-xs text-destructive">stale</span> : null}
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
