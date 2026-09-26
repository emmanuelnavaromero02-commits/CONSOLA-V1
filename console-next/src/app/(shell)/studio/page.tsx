"use client";

import Link from "next/link";
import { BookOpen, LayoutGrid, Library } from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore, type KeyboardEvent as ReactKeyboardEvent } from "react";

import { nextTabIndexForKey } from "@/app/(shell)/operational-intelligence/tablist-a11y";
import { DagGraph } from "@/components/studio/DagGraph";
import { DagsPanel } from "@/components/studio/DagsPanel";
import { EntitiesPanel } from "@/components/studio/EntitiesPanel";
import { LayersPanel } from "@/components/studio/LayersPanel";
import { RefinePanel } from "@/components/studio/RefinePanel";
import { StudioAssistant } from "@/components/studio/StudioAssistant";
import { StudioTopBar } from "@/components/studio/StudioTopBar";
import { Notice } from "@/components/studio/ui";
import { useDatasets } from "@/lib/monitor/hooks";
import { studioErrorMessage } from "@/lib/studio/client";
import { formatCount, plural } from "@/lib/studio/format";
import { useDagGraph, useStudioCartridges, useStudioManifest } from "@/lib/studio/hooks";
import {
  isSectionId,
  sectionById,
  sectionCounts,
  sectionHint,
  STUDIO_SECTIONS,
  type StudioSectionId,
} from "@/lib/studio/sections";
import type { StudioEditorTarget } from "@/lib/studio/types";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/data/inventory", label: "Catálogo semántico", icon: Library },
  { href: "/copilot/knowledge", label: "Base de conocimiento (RAG)", icon: BookOpen },
  { href: "/analytics", label: "Apps analíticas", icon: LayoutGrid },
];

function subscribeToNothing(): () => void {
  return () => undefined;
}

function useQueryParam(name: string): string | null {
  return useSyncExternalStore(
    subscribeToNothing,
    () => new URLSearchParams(window.location.search).get(name),
    () => null,
  );
}

function replaceQueryParam(name: string, value: string) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  url.searchParams.set(name, value);
  window.history.replaceState(window.history.state, "", url.toString());
}

export default function StudioPage() {
  const cartridges = useStudioCartridges();
  const requested = useQueryParam("cartridge");
  const tabParam = useQueryParam("tab");
  const [chosen, setChosen] = useState<string | null>(null);
  const [tabChoice, setTabChoice] = useState<StudioSectionId | null>(null);
  const tab: StudioSectionId = tabChoice ?? (isSectionId(tabParam) ? tabParam : "grafo");
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [editorTarget, setEditorTarget] = useState<StudioEditorTarget | null>(null);
  const [focusRequest, setFocusRequest] = useState(0);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const list = cartridges.data ?? [];
  const activeId =
    chosen
    ?? (requested && list.some((item) => item.id === requested) ? requested : null)
    ?? list[0]?.id
    ?? null;
  const manifest = useStudioManifest(activeId);
  const graph = useDagGraph(activeId);
  const datasets = useDatasets();
  const activeSection = sectionById(tab);
  const counts = sectionCounts({
    cartridge: activeId,
    graph: graph.data,
    manifest: manifest.data,
    datasets: datasets.data,
  });
  const sourceName = manifest.data?.name ?? list.find((item) => item.id === activeId)?.name ?? null;
  const activeHint = sectionHint(tab, { sourceName });

  useEffect(() => {
    if (focusRequest) panelRef.current?.focus();
  }, [focusRequest]);

  function selectCartridge(id: string) {
    setChosen(id);
    setEditorTarget(null);
    replaceQueryParam("cartridge", id);
  }

  function selectTab(id: StudioSectionId) {
    setTabChoice(id);
    replaceQueryParam("tab", id);
  }

  function chooseTab(id: StudioSectionId) {
    if (id === tab) return;
    setEditorTarget(null);
    selectTab(id);
  }

  function onTabKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>, index: number) {
    const next = nextTabIndexForKey(event.key, index, STUDIO_SECTIONS.length);
    if (next === null) return;
    event.preventDefault();
    chooseTab(STUDIO_SECTIONS[next].id);
    tabRefs.current[next]?.focus();
  }

  function openSection(id: StudioSectionId) {
    selectTab(id);
    setFocusRequest((value) => value + 1);
  }

  function openEditor(target: StudioEditorTarget) {
    setEditorTarget(target);
    openSection("refinar");
  }

  function openDeploy() {
    openSection("dags");
  }

  return (
    <main className="mx-auto max-w-[1600px] space-y-5 px-4 py-6 sm:px-6">
      <StudioTopBar
        cartridges={list}
        loading={cartridges.isLoading}
        error={cartridges.isError ? studioErrorMessage(cartridges.error, "Error al consultar /studio/cartridges.") : null}
        onRetry={() => cartridges.refetch()}
        activeId={activeId}
        onSelect={selectCartridge}
        manifest={manifest.data}
        assistantOpen={assistantOpen}
        onToggleAssistant={() => setAssistantOpen((value) => !value)}
        onDeploy={openDeploy}
      />

      <div className={cn("grid grid-cols-1 gap-4", assistantOpen && "xl:grid-cols-[minmax(0,1fr)_400px]")}>
        <section className="min-w-0 rounded-xl border bg-card shadow-sm">
          <div className="space-y-2 border-b p-2">
            <div role="tablist" aria-label="Secciones de Studio" className="flex flex-wrap gap-1">
              {STUDIO_SECTIONS.map((item, index) => {
                const active = tab === item.id;
                const count = counts[item.id];
                const hint = sectionHint(item.id, { sourceName });
                return (
                  <button
                    key={item.id}
                    ref={(element) => {
                      tabRefs.current[index] = element;
                    }}
                    type="button"
                    role="tab"
                    id={`studio-tab-${item.id}`}
                    aria-selected={active}
                    aria-controls={`studio-panel-${item.id}`}
                    aria-describedby={`studio-tab-hint-${item.id}`}
                    title={hint}
                    tabIndex={active ? 0 : -1}
                    onClick={() => chooseTab(item.id)}
                    onKeyDown={(event) => onTabKeyDown(event, index)}
                    className={cn(
                      "inline-flex min-h-[44px] items-center gap-2 rounded-lg px-3 text-sm font-medium transition-colors",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      active
                        ? "bg-primary text-primary-foreground shadow-sm"
                        : "text-muted-foreground hover:bg-accent/10 hover:text-foreground",
                    )}
                  >
                    <item.icon aria-hidden className="h-4 w-4 shrink-0" />
                    <span data-tab-label>{item.label}</span>
                    {count !== null ? (
                      <span
                        data-tab-count
                        aria-hidden
                        className={cn(
                          "rounded-full px-1.5 py-0.5 text-[11px] font-semibold tabular-nums",
                          active ? "bg-primary-foreground/20 text-primary-foreground" : "bg-muted text-muted-foreground",
                        )}
                      >
                        {formatCount(count)}
                      </span>
                    ) : null}
                  </button>
                );
              })}
            </div>
            <div hidden>
              {STUDIO_SECTIONS.map((item) => {
                const count = counts[item.id];
                return (
                  <span key={item.id} id={`studio-tab-hint-${item.id}`}>
                    {sectionHint(item.id, { sourceName })}
                    {count !== null ? `. ${plural(count, item.countNoun.one, item.countNoun.other)}` : ""}
                  </span>
                );
              })}
            </div>
            <p id="studio-section-hint" className="px-2 pb-1 text-sm text-muted-foreground">
              {activeHint}
            </p>
          </div>
          <div
            ref={panelRef}
            role="tabpanel"
            id={`studio-panel-${activeSection.id}`}
            aria-labelledby={`studio-tab-${activeSection.id}`}
            aria-describedby="studio-section-hint"
            tabIndex={0}
            data-testid="studio-panel"
            className="p-4 focus-visible:outline-none"
          >
            {!activeId ? (
              cartridges.isLoading ? (
                <p className="text-sm text-muted-foreground">Cargando cartuchos…</p>
              ) : (
                <Notice>No hay cartuchos visibles para tu usuario. Crea o importa uno para empezar.</Notice>
              )
            ) : (
              <>
                {manifest.isError ? (
                  <div className="mb-4">
                    <Notice tone="warning" title="No se pudo leer el manifiesto del cartucho.">
                      {studioErrorMessage(manifest.error, "Error al consultar /studio/cartridges/{id}.")}
                    </Notice>
                  </div>
                ) : null}
                {activeSection.id === "grafo" ? (
                  <DagGraph
                    key={activeId}
                    cartridge={activeId}
                    manifest={manifest.data}
                    onOpenEditor={openEditor}
                    onOpenSection={openSection}
                  />
                ) : null}
                {activeSection.id === "dags" ? <DagsPanel key={activeId} cartridge={activeId} manifest={manifest.data} /> : null}
                {activeSection.id === "entidades" ? (
                  <EntitiesPanel key={activeId} cartridge={activeId} manifest={manifest.data} />
                ) : null}
                {activeSection.id === "refinar" ? (
                  <RefinePanel
                    key={`${activeId}:${editorTarget?.dataset ?? ""}:${editorTarget?.entity ?? ""}`}
                    cartridge={activeId}
                    manifest={manifest.data}
                    initialTarget={editorTarget}
                  />
                ) : null}
                {activeSection.id === "capas" ? <LayersPanel key={activeId} cartridge={activeId} /> : null}
              </>
            )}
          </div>
        </section>
        {assistantOpen ? (
          <div id="studio-assistant-region">
            <StudioAssistant
              key={activeId ?? "sin-cartucho"}
              cartridge={activeId}
              step={activeSection.step}
              manifest={manifest.data}
              onClose={() => setAssistantOpen(false)}
            />
          </div>
        ) : null}
      </div>

      <nav aria-label="Herramientas relacionadas" className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {LINKS.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            className="flex min-h-[44px] items-center gap-3 rounded-lg border bg-card p-4 text-sm font-medium shadow-sm hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <link.icon aria-hidden className="h-4 w-4 text-primary" />
            {link.label}
          </Link>
        ))}
      </nav>
    </main>
  );
}
