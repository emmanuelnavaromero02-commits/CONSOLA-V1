import { Maximize2, ZoomIn, ZoomOut } from "lucide-react";
import { useMemo, useState } from "react";

import {
  GRAPH_MAX_NODES,
  cardinalitySentence,
  circularLayout,
  type CatalogEdge,
} from "@/lib/catalog/relations";
import { IDENTITY_VIEW, ZOOM_STEP, atMaxZoom, atMinZoom, zoomAt, zoomPercent, type ViewState } from "@/lib/studio/viewport";

interface RelationsGraphProps {
  edges: readonly CatalogEdge[];
  labelOf: (name: string) => string;
  max?: number;
  size?: number;
}

function shortLabel(text: string, limit = 22): string {
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

export function RelationsGraph({ edges, labelOf, max = GRAPH_MAX_NODES, size = 640 }: RelationsGraphProps) {
  const layout = useMemo(() => circularLayout(edges, max, size), [edges, max, size]);
  const [view, setView] = useState<ViewState>(IDENTITY_VIEW);
  const positions = useMemo(() => new Map(layout.nodes.map((node) => [node.id, node])), [layout.nodes]);
  const center = { x: size / 2, y: size / 2 };
  const drawn = edges.filter((edge) => positions.has(edge.from) && positions.has(edge.to));

  if (!layout.nodes.length) {
    return <p className="px-4 py-6 text-sm text-muted-foreground">Sin relaciones para graficar todavía.</p>;
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-end gap-1">
        <button
          type="button"
          onClick={() => setView((current) => zoomAt(current, 1 / ZOOM_STEP, center))}
          disabled={atMinZoom(view)}
          aria-label="Alejar"
          className="inline-flex h-9 w-9 items-center justify-center rounded-md border bg-background hover:bg-accent/5 disabled:opacity-50"
        >
          <ZoomOut aria-hidden className="h-4 w-4" />
        </button>
        <span className="w-12 text-center text-xs tabular-nums text-muted-foreground">{zoomPercent(view.k)}%</span>
        <button
          type="button"
          onClick={() => setView((current) => zoomAt(current, ZOOM_STEP, center))}
          disabled={atMaxZoom(view)}
          aria-label="Acercar"
          className="inline-flex h-9 w-9 items-center justify-center rounded-md border bg-background hover:bg-accent/5 disabled:opacity-50"
        >
          <ZoomIn aria-hidden className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={() => setView(IDENTITY_VIEW)}
          aria-label="Ajustar a la vista"
          className="inline-flex h-9 w-9 items-center justify-center rounded-md border bg-background hover:bg-accent/5"
        >
          <Maximize2 aria-hidden className="h-4 w-4" />
        </button>
      </div>
      <div className="overflow-hidden rounded-md border bg-background">
        <svg
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          role="img"
          aria-label={`Grafo de relaciones: ${layout.nodes.length} tablas y ${drawn.length} relaciones`}
          className="h-auto w-full"
        >
          <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
            {drawn.map((edge) => {
              const from = positions.get(edge.from)!;
              const to = positions.get(edge.to)!;
              const mid = { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 };
              return (
                <g key={`${edge.from}.${edge.fromColumn}>${edge.to}.${edge.toColumn}`} data-origin={edge.origin}>
                  <title>{cardinalitySentence(edge, labelOf)}</title>
                  <line
                    x1={from.x}
                    y1={from.y}
                    x2={to.x}
                    y2={to.y}
                    className={edge.origin === "copilot" ? "stroke-violet-500" : "stroke-foreground/50"}
                    strokeWidth={1.5}
                    strokeDasharray={edge.origin === "copilot" ? "6 4" : undefined}
                  />
                  {edge.cardinality ? (
                    <text x={mid.x} y={mid.y - 4} textAnchor="middle" className="fill-muted-foreground text-[10px]">
                      {edge.cardinality}
                    </text>
                  ) : null}
                </g>
              );
            })}
            {layout.nodes.map((node) => {
              const label = labelOf(node.id);
              return (
                <g key={node.id}>
                  <title>{label}</title>
                  <circle cx={node.x} cy={node.y} r={7} className="fill-primary" />
                  <text
                    x={node.x}
                    y={node.y + (node.y >= size / 2 ? 20 : -12)}
                    textAnchor="middle"
                    className="fill-foreground text-[11px]"
                  >
                    {shortLabel(label)}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>
      </div>
      <p className="text-xs text-muted-foreground">
        Línea continua: relación registrada o incluida en la fuente de datos. Línea punteada: detectada por el Copiloto.
        {layout.truncated ? ` Se muestran las ${max} tablas con más relaciones de ${layout.total}.` : ""}
      </p>
    </div>
  );
}
