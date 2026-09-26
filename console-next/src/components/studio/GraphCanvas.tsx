"use client";

import { Maximize2, ZoomIn, ZoomOut } from "lucide-react";
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";

import { plural } from "@/lib/studio/format";
import { GRAPH_NODE_HEIGHT, GRAPH_NODE_WIDTH, kindLabel, type GraphLayout } from "@/lib/studio/graph-layout";
import {
  edgeRole,
  nodeCaption,
  nodeDisplayName,
  nodeIcon,
  nodeStage,
  smartTruncate,
  STAGE_LABEL,
  STAGE_STYLE,
  STAGE_TEXT,
} from "@/lib/studio/graph-view";
import { DESKTOP_QUERY, useMediaQuery } from "@/lib/studio/media";
import type { DagGraphNode } from "@/lib/studio/types";
import {
  actualSizeView,
  atMaxZoom,
  atMinZoom,
  FIT_PADDING,
  fitView,
  PAN_STEP,
  panBy,
  revealBox,
  wheelFactor,
  ZOOM_STEP,
  zoomAt,
  zoomPercent,
  type Point,
  type Size,
  type ViewState,
} from "@/lib/studio/viewport";
import { cn } from "@/lib/utils";

export type GoldDot = { state: "ready" } | { state: "stale"; reason: string | null };

const ARROW = "studio-graph-arrow";
const ARROW_IN = "studio-graph-arrow-in";
const ARROW_OUT = "studio-graph-arrow-out";
const DOTS = "studio-graph-dots";
const GLOW = "[filter:drop-shadow(0_0_3px_currentColor)]";
export const DRAWER_WIDTH = 420;

const controlClass = cn(
  "inline-flex h-10 min-w-10 items-center justify-center rounded-lg px-2 text-xs font-semibold text-foreground",
  "hover:bg-accent/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  "disabled:cursor-not-allowed disabled:opacity-40",
);

interface DragState {
  id: number | undefined;
  x: number;
  y: number;
  view: ViewState;
}

function nodeText(node: DagGraphNode): string {
  return String(node.label || node.id);
}

export function GraphCanvas({
  cartridge,
  layout,
  selectedId,
  hoveredId,
  drawerOpen,
  goldDot,
  onSelect,
  onHover,
  registerNode,
  children,
}: {
  cartridge: string;
  layout: GraphLayout;
  selectedId: string | null;
  hoveredId: string | null;
  drawerOpen: boolean;
  goldDot: (node: DagGraphNode) => GoldDot | null;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
  registerNode: (id: string, element: SVGGElement | null) => void;
  children?: ReactNode;
}) {
  const hintId = useId();
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<SVGSVGElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const pointerFocusRef = useRef(false);
  const desktop = useMediaQuery(DESKTOP_QUERY, true);
  const [view, setView] = useState<ViewState | null>(null);
  const [size, setSize] = useState<Size>({ width: 0, height: 0 });
  const [dragging, setDragging] = useState(false);
  const content = useMemo<Size>(() => ({ width: layout.width, height: layout.height }), [layout.width, layout.height]);
  const fitted = useMemo(() => fitView(content, size), [content, size]);
  const fittedRef = useRef(fitted);
  const current = view ?? fitted;
  const activeId = hoveredId ?? selectedId;

  const relations = useMemo(() => {
    const counts = new Map<string, { incoming: number; outgoing: number }>();
    const bump = (id: string, key: "incoming" | "outgoing") => {
      const value = counts.get(id) ?? { incoming: 0, outgoing: 0 };
      value[key] += 1;
      counts.set(id, value);
    };
    for (const edge of layout.edges) {
      bump(edge.source, "outgoing");
      bump(edge.target, "incoming");
    }
    return counts;
  }, [layout.edges]);

  useEffect(() => {
    fittedRef.current = fitted;
  }, [fitted]);

  useEffect(() => {
    const element = wrapperRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (!box) return;
      setSize((previous) =>
        previous.width === box.width && previous.height === box.height ? previous : { width: box.width, height: box.height },
      );
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const element = canvasRef.current;
    if (!element) return;
    const canvas: SVGSVGElement = element;
    function onWheel(event: WheelEvent) {
      if (!(event.target instanceof Node) || !canvas.contains(event.target)) return;
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const point = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      const factor = wheelFactor(event.deltaY, event.deltaMode);
      setView((value) => zoomAt(value ?? fittedRef.current, factor, point));
    }
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, []);

  function centre(): Point {
    return { x: size.width / 2, y: size.height / 2 };
  }

  function zoomBy(factor: number) {
    const point = centre();
    setView((value) => zoomAt(value ?? fitted, factor, point));
  }

  function panView(dx: number, dy: number) {
    setView((value) => panBy(value ?? fitted, dx, dy));
  }

  function resetToActualSize() {
    setView(actualSizeView(content, size));
  }

  function revealNode(x: number, y: number) {
    if (pointerFocusRef.current) return;
    const inset = drawerOpen && desktop ? DRAWER_WIDTH : 0;
    const box = { x, y, width: GRAPH_NODE_WIDTH, height: GRAPH_NODE_HEIGHT };
    setView((value) => {
      const base = value ?? fitted;
      const next = revealBox(base, box, size, FIT_PADDING, inset);
      return next === base ? value : next;
    });
  }

  function onKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget) return;
    switch (event.key) {
      case "+":
      case "=":
        zoomBy(ZOOM_STEP);
        break;
      case "-":
      case "_":
        zoomBy(1 / ZOOM_STEP);
        break;
      case "0":
        resetToActualSize();
        break;
      case "f":
      case "F":
        setView(null);
        break;
      case "ArrowRight":
        panView(-PAN_STEP, 0);
        break;
      case "ArrowLeft":
        panView(PAN_STEP, 0);
        break;
      case "ArrowDown":
        panView(0, -PAN_STEP);
        break;
      case "ArrowUp":
        panView(0, PAN_STEP);
        break;
      default:
        return;
    }
    event.preventDefault();
  }

  function startPan(event: ReactPointerEvent<SVGRectElement>) {
    if (event.button !== undefined && event.button !== 0) return;
    const target = event.currentTarget;
    if (event.pointerId !== undefined && typeof target.setPointerCapture === "function") {
      try {
        target.setPointerCapture(event.pointerId);
      } catch {
        // Pointer capture is optional: panning still follows the svg's own move events.
      }
    }
    dragRef.current = { id: event.pointerId, x: event.clientX, y: event.clientY, view: current };
    setDragging(true);
  }

  function movePan(event: ReactPointerEvent<SVGSVGElement>) {
    const drag = dragRef.current;
    if (!drag || drag.id !== event.pointerId) return;
    setView(panBy(drag.view, event.clientX - drag.x, event.clientY - drag.y));
  }

  function endPan(event: ReactPointerEvent<SVGSVGElement>) {
    const drag = dragRef.current;
    if (!drag || drag.id !== event.pointerId) return;
    dragRef.current = null;
    setDragging(false);
  }

  const transform = `translate(${current.x} ${current.y}) scale(${current.k})`;

  return (
    <div
      ref={wrapperRef}
      data-graph-canvas
      role="region"
      aria-label="Lienzo del Mapa del Flujo"
      aria-describedby={hintId}
      tabIndex={0}
      onKeyDown={onKeyDown}
      className={cn(
        "relative h-[clamp(420px,65vh,720px)] overflow-hidden rounded-xl border bg-background",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      )}
    >
      <p id={hintId} className="sr-only">
        Arrastra el fondo para mover el mapa y usa la rueda del mouse o los controles para acercar o alejar. Con el
        lienzo enfocado: más y menos cambian el zoom, cero lo deja al 100 %, F lo ajusta y las flechas lo desplazan.
      </p>
      <svg
        ref={canvasRef}
        data-testid="dag-graph"
        role="group"
        aria-label={`Grafo del cartucho ${cartridge}`}
        className={cn("block h-full w-full touch-none select-none", dragging ? "cursor-grabbing" : "cursor-grab")}
        onPointerMove={movePan}
        onPointerUp={endPan}
        onPointerCancel={endPan}
      >
        <defs>
          <pattern id={DOTS} width={20} height={20} patternUnits="userSpaceOnUse" patternTransform={transform}>
            <circle cx={1} cy={1} r={1} className="fill-muted-foreground/25" />
          </pattern>
          {[
            { id: ARROW, className: "fill-muted-foreground" },
            { id: ARROW_IN, className: "fill-sky-500" },
            { id: ARROW_OUT, className: "fill-emerald-500" },
          ].map((marker) => (
            <marker
              key={marker.id}
              id={marker.id}
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" className={marker.className} />
            </marker>
          ))}
        </defs>
        <rect data-canvas-bg width="100%" height="100%" fill={`url(#${DOTS})`} onPointerDown={startPan} />
        <g data-testid="dag-graph-viewport" transform={transform}>
          {layout.columns.map((column) => (
            <text
              key={column.depth}
              x={column.x}
              y={28}
              className="pointer-events-none fill-muted-foreground text-[11px] font-semibold uppercase tracking-wide"
            >
              {column.label}
            </text>
          ))}
          {layout.edges.map((edge) => {
            const role = edgeRole(edge, activeId);
            return (
              <path
                key={`${edge.source}->${edge.target}`}
                d={edge.path}
                markerEnd={`url(#${role === "in" ? ARROW_IN : role === "out" ? ARROW_OUT : ARROW})`}
                data-active={role ? "true" : "false"}
                data-direction={role ?? undefined}
                className={cn(
                  "pointer-events-none fill-none motion-safe:transition-[opacity,stroke] motion-safe:duration-150",
                  role === "in" && cn("stroke-sky-500 stroke-[2.5] text-sky-500", GLOW),
                  role === "out" && cn("stroke-emerald-500 stroke-[2.5] text-emerald-500", GLOW),
                  !role && "stroke-muted-foreground stroke-[1.5]",
                  !role && (activeId ? "opacity-25" : "opacity-60"),
                )}
              />
            );
          })}
          {layout.nodes.map(({ node, x, y }) => {
            const stage = nodeStage(node);
            const Icon = nodeIcon(node);
            const label = nodeText(node);
            const name = nodeDisplayName(node);
            const caption = nodeCaption(node);
            const counts = relations.get(node.id) ?? { incoming: 0, outgoing: 0 };
            const isSelected = selectedId === node.id;
            const isActive = activeId === node.id;
            const dot = stage === "oro" ? goldDot(node) : null;
            return (
              <g
                key={node.id}
                ref={(element) => registerNode(node.id, element)}
                data-node-id={node.id}
                data-kind={node.kind}
                data-stage={stage}
                role="button"
                tabIndex={0}
                aria-pressed={isSelected}
                aria-label={`${kindLabel(node.kind)}: ${label}`}
                transform={`translate(${x} ${y})`}
                className="cursor-pointer focus:outline-none [&:focus-visible>rect]:stroke-primary [&:focus-visible>rect]:stroke-[3]"
                onClick={() => onSelect(node.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelect(node.id);
                  }
                }}
                onMouseEnter={() => onHover(node.id)}
                onMouseLeave={() => onHover(null)}
                onPointerDown={() => {
                  pointerFocusRef.current = true;
                }}
                onPointerUp={() => {
                  pointerFocusRef.current = false;
                }}
                onPointerCancel={() => {
                  pointerFocusRef.current = false;
                }}
                onFocus={() => {
                  onHover(node.id);
                  revealNode(x, y);
                }}
                onBlur={() => {
                  pointerFocusRef.current = false;
                  onHover(null);
                }}
              >
                <title>
                  {`${name}\n${STAGE_LABEL[stage]} · ${caption}\n${plural(counts.incoming, "entrada", "entradas")} · ${plural(counts.outgoing, "salida", "salidas")}`}
                </title>
                <rect
                  width={GRAPH_NODE_WIDTH}
                  height={GRAPH_NODE_HEIGHT}
                  rx={10}
                  className={cn(
                    "stroke-[1.5] motion-safe:transition-[stroke-width] motion-safe:duration-150",
                    STAGE_STYLE[stage],
                    isActive && "stroke-[2.5]",
                    isSelected && "stroke-primary stroke-[3]",
                  )}
                />
                <Icon aria-hidden x={12} y={12} width={16} height={16} className={STAGE_TEXT[stage]} />
                <text x={34} y={24} className="fill-muted-foreground text-[10px] font-semibold uppercase tracking-wide">
                  {caption}
                </text>
                <text x={12} y={44} className="fill-foreground text-[12px] font-medium">
                  {smartTruncate(name, 24)}
                </text>
                <g transform={`translate(${GRAPH_NODE_WIDTH - 34} 10)`}>
                  <rect width={24} height={16} rx={8} className="fill-muted stroke-border" />
                  <text x={12} y={12} textAnchor="middle" className="fill-muted-foreground text-[10px] font-semibold">
                    {counts.incoming + counts.outgoing}
                  </text>
                </g>
                {dot ? (
                  <circle
                    data-status-dot={dot.state}
                    cx={GRAPH_NODE_WIDTH - 46}
                    cy={18}
                    r={4}
                    className={dot.state === "ready" ? "fill-emerald-500 motion-safe:animate-pulse" : "fill-amber-500"}
                  >
                    <title>
                      {dot.state === "ready"
                        ? "Listo: materializado y sin marcas de desactualización"
                        : `Desactualizado${dot.reason ? `: ${dot.reason}` : ""}`}
                    </title>
                  </circle>
                ) : null}
              </g>
            );
          })}
        </g>
      </svg>
      <div
        role="group"
        aria-label="Controles del lienzo"
        className={cn(
          "absolute bottom-3 right-3 z-10 flex items-center gap-0.5 rounded-xl border bg-background/70 p-1 shadow-lg backdrop-blur-md",
          drawerOpen && "md:right-[436px]",
        )}
      >
        <button
          type="button"
          aria-label="Alejar"
          title="Alejar (−)"
          className={controlClass}
          disabled={atMinZoom(current)}
          onClick={() => zoomBy(1 / ZOOM_STEP)}
        >
          <ZoomOut aria-hidden className="h-4 w-4" />
        </button>
        <span
          data-testid="dag-graph-zoom"
          aria-live="polite"
          className="min-w-[3.25rem] text-center text-xs font-semibold tabular-nums text-muted-foreground"
        >
          {zoomPercent(current.k)} %
        </span>
        <button
          type="button"
          aria-label="Acercar"
          title="Acercar (+)"
          className={controlClass}
          disabled={atMaxZoom(current)}
          onClick={() => zoomBy(ZOOM_STEP)}
        >
          <ZoomIn aria-hidden className="h-4 w-4" />
        </button>
        <span aria-hidden className="mx-0.5 h-6 w-px bg-border" />
        <button
          type="button"
          aria-label="Centrar y ajustar"
          title="Centrar y ajustar (F)"
          className={controlClass}
          onClick={() => setView(null)}
        >
          <Maximize2 aria-hidden className="h-4 w-4" />
        </button>
        <button
          type="button"
          aria-label="1:1 · Restablecer al 100 %"
          title="Restablecer al 100 % (0)"
          className={controlClass}
          onClick={resetToActualSize}
        >
          1:1
        </button>
      </div>
      {children}
    </div>
  );
}
