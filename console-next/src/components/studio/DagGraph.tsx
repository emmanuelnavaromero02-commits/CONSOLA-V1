"use client";

import { RefreshCw } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { useDatasets } from "@/lib/monitor/hooks";
import type { DatasetSummary } from "@/lib/monitor/types";
import { studioErrorMessage } from "@/lib/studio/client";
import { plural } from "@/lib/studio/format";
import { layoutDagGraph } from "@/lib/studio/graph-layout";
import { nodeRef, STAGE_LABEL, STAGE_STYLE, STAGES } from "@/lib/studio/graph-view";
import { isGoldReady } from "@/lib/studio/health";
import { useDagGraph } from "@/lib/studio/hooks";
import type { StudioSectionId } from "@/lib/studio/sections";
import type { DagGraphNode, StudioEditorTarget, StudioManifest } from "@/lib/studio/types";
import { cn } from "@/lib/utils";

import { GraphCanvas, type GoldDot } from "./GraphCanvas";
import { NodeDrawer } from "./NodeDrawer";
import { buttonClass, Notice, Spinner } from "./ui";

export function DagGraph({
  cartridge,
  manifest,
  onOpenEditor,
  onOpenSection,
}: {
  cartridge: string;
  manifest?: StudioManifest | null;
  onOpenEditor?: (target: StudioEditorTarget) => void;
  onOpenSection?: (id: StudioSectionId) => void;
}) {
  const graph = useDagGraph(cartridge);
  const datasets = useDatasets();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const nodeElements = useRef(new Map<string, SVGGElement>());
  const layout = useMemo(
    () => layoutDagGraph(graph.data?.nodes ?? [], graph.data?.edges ?? []),
    [graph.data],
  );
  const byId = useMemo(() => new Map(layout.nodes.map((item) => [item.node.id, item.node])), [layout.nodes]);
  const datasetsByName = useMemo(
    () => new Map<string, DatasetSummary>((datasets.data ?? []).map((dataset) => [dataset.name, dataset])),
    [datasets.data],
  );
  const selected = selectedId ? byId.get(selectedId) : undefined;

  function goldDot(node: DagGraphNode): GoldDot | null {
    const summary = datasetsByName.get(nodeRef(node).name);
    if (!summary) return null;
    if (isGoldReady(summary)) return { state: "ready" };
    if (summary.is_stale === true) return { state: "stale", reason: summary.staleness_reason ?? null };
    return null;
  }

  function registerNode(id: string, element: SVGGElement | null) {
    if (element) nodeElements.current.set(id, element);
    else nodeElements.current.delete(id);
  }

  function closeDrawer() {
    if (selectedId) nodeElements.current.get(selectedId)?.focus();
    setSelectedId(null);
  }

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
    <div className="space-y-3">
      <div role="group" aria-label="Leyenda" className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
        {STAGES.map((stage) => (
          <span key={stage} className="inline-flex items-center gap-1.5">
            <svg width="14" height="14" aria-hidden>
              <rect x="1" y="1" width="12" height="12" rx="3" className={cn("stroke-[1.5]", STAGE_STYLE[stage])} />
            </svg>
            {STAGE_LABEL[stage]}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden className="h-2 w-2 rounded-full bg-emerald-500" /> Oro listo
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden className="h-2 w-2 rounded-full bg-amber-500" /> Oro desactualizado
        </span>
        <span className="sm:ml-auto">
          {plural(layout.nodes.length, "nodo", "nodos")} · {plural(layout.edges.length, "relación", "relaciones")}
        </span>
      </div>
      <GraphCanvas
        cartridge={cartridge}
        layout={layout}
        selectedId={selected?.id ?? null}
        hoveredId={hoveredId && byId.has(hoveredId) ? hoveredId : null}
        drawerOpen={Boolean(selected)}
        goldDot={goldDot}
        onSelect={setSelectedId}
        onHover={setHoveredId}
        registerNode={registerNode}
      >
        {selected ? (
          <NodeDrawer
            cartridge={cartridge}
            manifest={manifest ?? null}
            node={selected}
            edges={layout.edges}
            byId={byId}
            datasetsByName={datasetsByName}
            onClose={closeDrawer}
            onSelect={setSelectedId}
            onOpenEditor={onOpenEditor}
            onOpenSection={onOpenSection}
          />
        ) : null}
      </GraphCanvas>
      <p className="text-xs text-muted-foreground">
        Arrastra el fondo para moverte, usa la rueda para acercar o alejar y selecciona un nodo para ver su detalle.
      </p>
    </div>
  );
}
