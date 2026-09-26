"use client";

import { GitFork, Info, Table, X, type LucideIcon } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { toast } from "sonner";

import { nextTabIndexForKey } from "@/app/(shell)/operational-intelligence/tablist-a11y";
import type { DatasetSummary } from "@/lib/monitor/types";
import { studioErrorMessage } from "@/lib/studio/client";
import { graphNeighbours } from "@/lib/studio/graph-layout";
import { nodeRef, nodeStage, STAGE_LABEL, STAGE_STYLE, STAGE_TEXT } from "@/lib/studio/graph-view";
import { useRefreshDataset } from "@/lib/studio/hooks";
import type { StudioSectionId } from "@/lib/studio/sections";
import type { DagGraphEdge, DagGraphNode, StudioEditorTarget, StudioManifest } from "@/lib/studio/types";
import { cn } from "@/lib/utils";

import { extractionMode, startExtraction, type ExtractionLaunch } from "./ExtractionTracker";
import { AboutPanel, InfoPanel, LineagePanel, manifestEntity, nodeTitle } from "./NodeDrawerTabs";
import { buttonClass, ConfirmDialog } from "./ui";

type DrawerTab = "about" | "lineage" | "info";

const TABS: Array<{ id: DrawerTab; label: string; icon: LucideIcon }> = [
  { id: "about", label: "¿De qué se trata este dato?", icon: Info },
  { id: "lineage", label: "¿Quién lo alimenta y quién lo usa?", icon: GitFork },
  { id: "info", label: "Ver información", icon: Table },
];

type Confirming = "refresh" | "extract";

export function NodeDrawer({
  cartridge,
  manifest,
  node,
  edges,
  byId,
  datasetsByName,
  onClose,
  onSelect,
  onOpenEditor,
  onOpenSection,
}: {
  cartridge: string;
  manifest: StudioManifest | null;
  node: DagGraphNode;
  edges: DagGraphEdge[];
  byId: Map<string, DagGraphNode>;
  datasetsByName: Map<string, DatasetSummary>;
  onClose: () => void;
  onSelect: (id: string) => void;
  onOpenEditor?: (target: StudioEditorTarget) => void;
  onOpenSection?: (id: StudioSectionId) => void;
}) {
  const baseId = useId();
  const titleId = `${baseId}-title`;
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [tab, setTab] = useState<DrawerTab>("about");
  const [confirming, setConfirming] = useState<{ nodeId: string; action: Confirming } | null>(null);
  const [launch, setLaunch] = useState<{ nodeId: string; value: ExtractionLaunch } | null>(null);
  const [extracting, setExtracting] = useState(false);
  const refresh = useRefreshDataset();
  const confirmOpenRef = useRef(false);
  const onCloseRef = useRef(onClose);
  const ref = nodeRef(node);
  const stage = nodeStage(node);
  const neighbours = graphNeighbours(node.id, edges);
  const action = confirming?.nodeId === node.id ? confirming.action : null;
  const currentLaunch = launch?.nodeId === node.id ? launch.value : null;
  const entity = ref.kind === "entity" ? manifestEntity(manifest, ref.name) : undefined;
  const mode = extractionMode(entity?.mode);

  useEffect(() => {
    confirmOpenRef.current = action !== null;
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    closeRef.current?.focus();
  }, [node.id]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape" || confirmOpenRef.current) return;
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      onCloseRef.current();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  function onTabKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>, index: number) {
    const next = nextTabIndexForKey(event.key, index, TABS.length);
    if (next === null) return;
    event.preventDefault();
    setTab(TABS[next].id);
    tabRefs.current[next]?.focus();
  }

  function runRefresh() {
    const name = ref.name;
    refresh.mutate(name, {
      onSuccess: (result) =>
        toast.success(
          typeof result?.row_count === "number"
            ? `Dataset ${name} materializado: ${result.row_count} filas.`
            : `Dataset ${name} materializado.`,
        ),
      onError: (error) => toast.error(studioErrorMessage(error, "No se pudo materializar el dataset.")),
      onSettled: () => setConfirming(null),
    });
  }

  async function runExtraction() {
    const nodeId = node.id;
    const name = ref.name;
    setExtracting(true);
    try {
      const value = await startExtraction(cartridge, name, mode);
      setLaunch({ nodeId, value });
      toast.success(`Extracción enviada para ${name}.`);
    } catch (error) {
      toast.error(studioErrorMessage(error, `No se pudo extraer ${name}.`));
    } finally {
      setExtracting(false);
      setConfirming(null);
    }
  }

  const source = typeof manifest?.name === "string" && manifest.name.trim() ? manifest.name.trim() : cartridge;

  return (
    <>
      <aside
        role="dialog"
        aria-labelledby={titleId}
        data-testid="dag-graph-detail"
        className={cn(
          "fixed inset-0 z-40 flex flex-col overflow-y-auto border-l bg-card shadow-xl",
          "motion-safe:animate-studio-drawer-in md:absolute md:inset-y-0 md:left-auto md:right-0 md:w-[420px]",
        )}
      >
        <header className="space-y-2 border-b p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 space-y-1">
              <span
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide",
                  STAGE_TEXT[stage],
                )}
              >
                <svg width="10" height="10" aria-hidden>
                  <rect x="1" y="1" width="8" height="8" rx="2" className={cn("stroke-[1.5]", STAGE_STYLE[stage])} />
                </svg>
                {STAGE_LABEL[stage]}
              </span>
              <h2 id={titleId} className="break-words text-lg font-semibold leading-tight">
                {nodeTitle(node, manifest)}
              </h2>
              <p className="break-all font-mono text-xs text-muted-foreground">{node.id}</p>
            </div>
            <button
              ref={closeRef}
              type="button"
              className={cn(buttonClass, "shrink-0 px-2")}
              aria-label="Cerrar detalle"
              title="Cerrar detalle (Esc)"
              onClick={onClose}
            >
              <X aria-hidden className="h-4 w-4" />
            </button>
          </div>
          <p className="text-xs font-medium text-muted-foreground">
            Entradas ({neighbours.incoming.length}) · Salidas ({neighbours.outgoing.length})
          </p>
        </header>
        <div role="tablist" aria-label="Secciones del detalle" className="grid grid-cols-3 gap-1 border-b bg-muted/30 p-2">
          {TABS.map((item, index) => {
            const active = tab === item.id;
            return (
              <button
                key={item.id}
                ref={(element) => {
                  tabRefs.current[index] = element;
                }}
                type="button"
                role="tab"
                id={`${baseId}-tab-${item.id}`}
                aria-selected={active}
                aria-controls={`${baseId}-panel-${item.id}`}
                tabIndex={active ? 0 : -1}
                onClick={() => setTab(item.id)}
                onKeyDown={(event) => onTabKeyDown(event, index)}
                className={cn(
                  "flex min-h-[44px] items-start gap-1.5 rounded-md px-2 py-2 text-left text-xs font-medium leading-tight",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  active ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:bg-background/60",
                )}
              >
                <item.icon aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>{item.label}</span>
              </button>
            );
          })}
        </div>
        <div
          role="tabpanel"
          id={`${baseId}-panel-${tab}`}
          aria-labelledby={`${baseId}-tab-${tab}`}
          tabIndex={0}
          className="flex-1 p-4 focus-visible:outline-none"
        >
          {tab === "about" ? (
            <AboutPanel cartridge={cartridge} node={node} manifest={manifest} datasetsByName={datasetsByName} />
          ) : null}
          {tab === "lineage" ? (
            <LineagePanel cartridge={cartridge} node={node} edges={edges} byId={byId} manifest={manifest} onSelect={onSelect} />
          ) : null}
          {tab === "info" ? (
            <InfoPanel
              cartridge={cartridge}
              node={node}
              manifest={manifest}
              launch={currentLaunch}
              refreshing={refresh.isPending || extracting}
              onOpenEditor={onOpenEditor}
              onOpenSection={onOpenSection}
              onRequestRefresh={() => setConfirming({ nodeId: node.id, action: "refresh" })}
              onRequestExtract={() => setConfirming({ nodeId: node.id, action: "extract" })}
            />
          ) : null}
        </div>
      </aside>
      <ConfirmDialog
        open={action === "refresh"}
        title="Forzar actualización del dataset"
        description={`Se volverá a materializar ${ref.name} con su definición SQL actual y sus datos se reemplazarán al terminar.`}
        confirmLabel="Actualizar ahora"
        pendingLabel="Actualizando…"
        pending={refresh.isPending}
        onConfirm={runRefresh}
        onCancel={() => setConfirming(null)}
        testId="refresh-dataset-dialog"
      />
      <ConfirmDialog
        open={action === "extract"}
        title="Forzar extracción de la tabla de origen"
        description={`Se lanzará una extracción ${mode === "full" ? "completa" : "incremental"} de ${ref.name} desde ${source}.`}
        confirmLabel="Lanzar extracción"
        pendingLabel="Lanzando…"
        pending={extracting}
        onConfirm={runExtraction}
        onCancel={() => setConfirming(null)}
        testId="extract-entity-dialog"
      />
    </>
  );
}
