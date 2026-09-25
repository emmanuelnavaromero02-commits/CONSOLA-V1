"use client";

import Link from "next/link";
import { BookOpen, Bot, LayoutGrid, Library } from "lucide-react";
import { useState, useSyncExternalStore } from "react";

import { CartridgeBar } from "@/components/studio/CartridgeBar";
import { DagGraph } from "@/components/studio/DagGraph";
import { DagsPanel } from "@/components/studio/DagsPanel";
import { EntitiesPanel } from "@/components/studio/EntitiesPanel";
import { LayersPanel } from "@/components/studio/LayersPanel";
import { RefinePanel } from "@/components/studio/RefinePanel";
import { StudioAssistant } from "@/components/studio/StudioAssistant";
import { buttonClass, Notice } from "@/components/studio/ui";
import { studioErrorMessage } from "@/lib/studio/client";
import { useStudioCartridges, useStudioManifest } from "@/lib/studio/hooks";
import { cn } from "@/lib/utils";

const TABS = [
  { id: "grafo", label: "Grafo", step: 1 },
  { id: "dags", label: "DAGs", step: 2 },
  { id: "entidades", label: "Entidades", step: 3 },
  { id: "refinar", label: "Refinar", step: 4 },
  { id: "capas", label: "Capas", step: 5 },
] as const;

type TabId = (typeof TABS)[number]["id"];

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

function isTab(value: string | null): value is TabId {
  return TABS.some((tab) => tab.id === value);
}

export default function StudioPage() {
  const cartridges = useStudioCartridges();
  const requested = useQueryParam("cartridge");
  const tabParam = useQueryParam("tab");
  const [chosen, setChosen] = useState<string | null>(null);
  const [tabChoice, setTab] = useState<TabId | null>(null);
  const tab: TabId = tabChoice ?? (isTab(tabParam) ? tabParam : "grafo");
  const [assistantOpen, setAssistantOpen] = useState(false);
  const list = cartridges.data ?? [];
  const activeId =
    chosen
    ?? (requested && list.some((item) => item.id === requested) ? requested : null)
    ?? list[0]?.id
    ?? null;
  const manifest = useStudioManifest(activeId);
  const activeTab = TABS.find((item) => item.id === tab) ?? TABS[0];

  function selectCartridge(id: string) {
    setChosen(id);
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      url.searchParams.set("cartridge", id);
      window.history.replaceState(window.history.state, "", url.toString());
    }
  }

  return (
    <main className="mx-auto max-w-[1600px] space-y-6 px-4 py-6 sm:px-6">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-3xl font-semibold tracking-tight">Studio</h1>
          <p className="text-sm text-muted-foreground">
            Configura DAGs, entidades, refinamiento de capas y datasets de cada cartucho.
          </p>
        </div>
        <button
          type="button"
          className={cn(buttonClass, assistantOpen && "border-primary text-primary")}
          aria-expanded={assistantOpen}
          aria-controls="studio-assistant-region"
          onClick={() => setAssistantOpen((value) => !value)}
        >
          <Bot aria-hidden className="h-4 w-4" /> Asistente de Studio
        </button>
      </header>

      <CartridgeBar
        cartridges={list}
        loading={cartridges.isLoading}
        error={cartridges.isError ? studioErrorMessage(cartridges.error, "Error al consultar /studio/cartridges.") : null}
        onRetry={() => cartridges.refetch()}
        activeId={activeId}
        onSelect={selectCartridge}
      />

      <div className={cn("grid grid-cols-1 gap-4", assistantOpen && "xl:grid-cols-[minmax(0,1fr)_400px]")}>
        <section className="min-w-0 rounded-lg border bg-card shadow-sm">
          <div role="tablist" aria-label="Secciones de Studio" className="flex flex-wrap gap-1 border-b p-2">
            {TABS.map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                id={`studio-tab-${item.id}`}
                aria-selected={tab === item.id}
                aria-controls={`studio-panel-${item.id}`}
                onClick={() => setTab(item.id)}
                className={cn(
                  "min-h-[44px] rounded-md px-4 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  tab === item.id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent/10",
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
          <div
            role="tabpanel"
            id={`studio-panel-${activeTab.id}`}
            aria-labelledby={`studio-tab-${activeTab.id}`}
            data-testid="studio-panel"
            className="p-4"
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
                {activeTab.id === "grafo" ? <DagGraph key={activeId} cartridge={activeId} /> : null}
                {activeTab.id === "dags" ? <DagsPanel key={activeId} cartridge={activeId} manifest={manifest.data} /> : null}
                {activeTab.id === "entidades" ? (
                  <EntitiesPanel key={activeId} cartridge={activeId} manifest={manifest.data} />
                ) : null}
                {activeTab.id === "refinar" ? (
                  <RefinePanel key={activeId} cartridge={activeId} manifest={manifest.data} />
                ) : null}
                {activeTab.id === "capas" ? <LayersPanel key={activeId} cartridge={activeId} /> : null}
              </>
            )}
          </div>
        </section>
        {assistantOpen ? (
          <div id="studio-assistant-region">
            <StudioAssistant
              key={activeId ?? "sin-cartucho"}
              cartridge={activeId}
              step={activeTab.step}
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
