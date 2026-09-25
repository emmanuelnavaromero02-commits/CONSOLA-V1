"use client";

import { RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";

import { studioErrorMessage } from "@/lib/studio/client";
import {
  GRAPH_NODE_HEIGHT,
  GRAPH_NODE_WIDTH,
  graphNeighbours,
  kindLabel,
  layoutDagGraph,
} from "@/lib/studio/graph-layout";
import { useDagGraph } from "@/lib/studio/hooks";
import type { DagGraphNode } from "@/lib/studio/types";
import { cn } from "@/lib/utils";

import { buttonClass, Notice, Spinner } from "./ui";

const KIND_STYLE: Record<string, string> = {
  cartridge: "fill-primary/10 stroke-primary",
  entity: "fill-card stroke-border",
  dag: "fill-amber-500/10 stroke-amber-500",
  dataset: "fill-success/10 stroke-success",
};

function shortText(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, Math.max(0, max - 1))}…` : value;
}

function nodeName(node: DagGraphNode | undefined, fallback: string): string {
  return String(node?.label || node?.id || fallback);
}

export function DagGraph({ cartridge }: { cartridge: string }) {
  const graph = useDagGraph(cartridge);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const layout = useMemo(
    () => layoutDagGraph(graph.data?.nodes ?? [], graph.data?.edges ?? []),
    [graph.data],
  );
  const byId = useMemo(() => new Map(layout.nodes.map((item) => [item.node.id, item.node])), [layout.nodes]);
  const selected = selectedId ? byId.get(selectedId) : undefined;
  const neighbours = selected ? graphNeighbours(selected.id, layout.edges) : null;

  if (graph.isLoading) {
    return (
      <p className="flex items-center gap-2 p-4 text-sm text-muted-foreground" aria-busy="true">
        <Spinner /> Cargando grafo del cartucho…
      </p>
    );
  }

  if (graph.isError) {
    return (
      <Notice
        tone="error"
        title="No se pudo cargar el grafo."
        action={
          <button type="button" className={buttonClass} onClick={() => graph.refetch()}>
            <RefreshCw aria-hidden className="h-4 w-4" /> Reintentar
          </button>
        }
      >
        {studioErrorMessage(graph.error, "Error al consultar /api/studio/dag-graph.")}
      </Notice>
    );
  }

  if (!layout.nodes.length) {
    return <Notice title="Sin nodos">El cartucho no reporta entidades, DAGs ni datasets.</Notice>;
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground" aria-label="Leyenda">
        {(["cartridge", "entity", "dag", "dataset"] as const).map((kind) => (
          <span key={kind} className="inline-flex items-center gap-1.5">
            <svg width="14" height="14" aria-hidden>
              <rect x="1" y="1" width="12" height="12" rx="3" className={cn("stroke-[1.5]", KIND_STYLE[kind])} />
            </svg>
            {kindLabel(kind)}
          </span>
        ))}
        <span>
          {layout.nodes.length} nodos · {layout.edges.length} relaciones
        </span>
      </div>
      <div className="overflow-auto rounded-lg border bg-background">
        <svg
          data-testid="dag-graph"
          role="group"
          aria-label={`Grafo del cartucho ${cartridge}`}
          width={layout.width}
          height={layout.height}
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          className="block"
        >
          <defs>
            <marker
              id="studio-graph-arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" className="fill-muted-foreground" />
            </marker>
          </defs>
          {layout.columns.map((column) => (
            <text
              key={column.depth}
              x={column.x}
              y={28}
              className="fill-muted-foreground text-[11px] font-semibold uppercase"
            >
              {column.label}
            </text>
          ))}
          {layout.edges.map((edge) => {
            const related = selected && (edge.source === selected.id || edge.target === selected.id);
            return (
              <path
                key={`${edge.source}->${edge.target}`}
                d={edge.path}
                markerEnd="url(#studio-graph-arrow)"
                className={cn(
                  "fill-none",
                  related ? "stroke-primary stroke-[2.5]" : "stroke-muted-foreground stroke-[1.5] opacity-50",
                )}
              />
            );
          })}
          {layout.nodes.map(({ node, x, y }) => {
            const isSelected = selected?.id === node.id;
            const label = nodeName(node, node.id);
            return (
              <g
                key={node.id}
                data-node-id={node.id}
                data-kind={node.kind}
                role="button"
                tabIndex={0}
                aria-pressed={isSelected}
                aria-label={`${kindLabel(node.kind)}: ${label}`}
                transform={`translate(${x} ${y})`}
                className="cursor-pointer focus:outline-none [&:focus-visible>rect]:stroke-primary [&:focus-visible>rect]:stroke-[3]"
                onClick={() => setSelectedId(node.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelectedId(node.id);
                  }
                }}
              >
                <rect
                  width={GRAPH_NODE_WIDTH}
                  height={GRAPH_NODE_HEIGHT}
                  rx={8}
                  className={cn(
                    "stroke-[1.5]",
                    KIND_STYLE[String(node.kind)] ?? "fill-card stroke-border",
                    isSelected && "stroke-primary stroke-[3]",
                  )}
                />
                <text x={12} y={22} className="fill-muted-foreground text-[10px] uppercase">
                  {kindLabel(node.kind)}
                </text>
                <text x={12} y={40} className="fill-foreground text-[12px] font-medium">
                  {shortText(label, 26)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      {selected && neighbours ? (
        <article aria-label="Detalle del nodo" data-testid="dag-graph-detail" className="rounded-md border bg-card p-4 text-sm">
          <p className="text-xs uppercase text-muted-foreground">{kindLabel(selected.kind)}</p>
          <h3 className="mt-1 break-all font-semibold">{nodeName(selected, selected.id)}</h3>
          <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{selected.id}</p>
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <p className="text-xs font-medium text-muted-foreground">Entradas ({neighbours.incoming.length})</p>
              <ul className="mt-1 space-y-0.5 text-xs">
                {neighbours.incoming.map((id) => (
                  <li key={id} className="break-all">{nodeName(byId.get(id), id)}</li>
                ))}
                {!neighbours.incoming.length ? <li className="text-muted-foreground">Ninguna</li> : null}
              </ul>
            </div>
            <div>
              <p className="text-xs font-medium text-muted-foreground">Salidas ({neighbours.outgoing.length})</p>
              <ul className="mt-1 space-y-0.5 text-xs">
                {neighbours.outgoing.map((id) => (
                  <li key={id} className="break-all">{nodeName(byId.get(id), id)}</li>
                ))}
                {!neighbours.outgoing.length ? <li className="text-muted-foreground">Ninguna</li> : null}
              </ul>
            </div>
          </div>
        </article>
      ) : (
        <p className="text-xs text-muted-foreground">Selecciona un nodo para ver sus relaciones.</p>
      )}
    </div>
  );
}
