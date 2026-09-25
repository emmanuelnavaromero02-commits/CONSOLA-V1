"use client";

import { ArrowLeft } from "lucide-react";
import { useEffect, useSyncExternalStore, type KeyboardEvent } from "react";

import { useSapB1Access } from "@/lib/sap-b1/hooks";
import { cn } from "@/lib/utils";

import { AgentsSection } from "./AgentsSection";
import { EntityModelSection } from "./EntityModelSection";
import { IndicatorsSection } from "./IndicatorsSection";
import { InfoBitSection } from "./InfoBitSection";
import { ParametersSection } from "./ParametersSection";
import { SemaforoSection } from "./SemaforoSection";
import { SetupSection } from "./SetupSection";
import { Notice } from "./ui";

export const SAP_B1_TABS = [
  { id: "puesta-en-marcha", label: "Puesta en marcha" },
  { id: "infobit", label: "InfoBit" },
  { id: "wisdombit", label: "WisdomBit" },
  { id: "knowledgebit", label: "KnowledgeBit" },
  { id: "agentes", label: "Agentes" },
  { id: "semaforo", label: "Semáforo" },
  { id: "parametros", label: "Parámetros", writeOnly: true },
] as const;

export type SapB1TabId = (typeof SAP_B1_TABS)[number]["id"];

function subscribeHash(onChange: () => void) {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

function readHash() {
  return window.location.hash.replace(/^#/, "");
}

function selectHash(id: string) {
  window.history.replaceState(null, "", `#${id}`);
  window.dispatchEvent(new Event("hashchange"));
}

function TabPanel({ tab, canWrite, canReadAgents }: { tab: SapB1TabId; canWrite: boolean; canReadAgents: boolean | null }) {
  switch (tab) {
    case "infobit":
      return <InfoBitSection />;
    case "wisdombit":
      return <EntityModelSection />;
    case "knowledgebit":
      return <IndicatorsSection />;
    case "agentes":
      return <AgentsSection canReadAgents={canReadAgents} />;
    case "semaforo":
      return <SemaforoSection />;
    case "parametros":
      return <ParametersSection canWrite={canWrite} />;
    default:
      return <SetupSection />;
  }
}

export function SapB1Page() {
  const { access, installed, canWrite, canReadAgents } = useSapB1Access();
  const hash = useSyncExternalStore(subscribeHash, readHash, () => "");
  const tabs = SAP_B1_TABS.filter((tab) => !("writeOnly" in tab) || canWrite);
  const active = (tabs.find((tab) => tab.id === hash)?.id ?? "puesta-en-marcha") as SapB1TabId;

  useEffect(() => {
    document.getElementById(`tab-${active}`)?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [active]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const index = tabs.findIndex((tab) => tab.id === active);
    const next = tabs[(index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    selectHash(next.id);
    document.getElementById(`tab-${next.id}`)?.focus();
  };

  return (
    <main className="mx-auto w-full max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8" aria-label="SAP Business One">
      <div className="border-b pb-4 dark:border-sky-400/15">
        <a href="/control-room" className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline">
          <ArrowLeft aria-hidden className="h-4 w-4" />
          Control Room
        </a>
        <h1 className="mt-1 text-2xl font-semibold text-foreground dark:text-white">SAP Business One</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Puesta en marcha, cargas, modelo de entidades, indicadores, agentes y semáforo del día. Solo recomendaciones: nada se escribe en Business One.
        </p>
      </div>

      {access.data && !installed ? (
        <div className="mt-4">
          <Notice tone="warning" title="Cartucho no habilitado">
            SAP Business One no está instalado o no está habilitado para tu usuario en este workspace; algunas secciones no tendrán datos.
          </Notice>
        </div>
      ) : null}

      <div
        role="tablist"
        aria-label="Secciones de SAP Business One"
        onKeyDown={onKeyDown}
        className="mt-4 flex gap-1 overflow-x-auto border-b pb-px dark:border-sky-400/15"
      >
        {tabs.map((tab) => (
          <button
            key={tab.id}
            id={`tab-${tab.id}`}
            type="button"
            role="tab"
            aria-selected={tab.id === active}
            aria-controls={`panel-${tab.id}`}
            tabIndex={tab.id === active ? 0 : -1}
            onClick={() => selectHash(tab.id)}
            className={cn(
              "min-h-[40px] shrink-0 whitespace-nowrap border-b-2 px-3 text-sm font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              tab.id === active
                ? "border-primary text-foreground dark:text-white"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div id={`panel-${active}`} role="tabpanel" aria-labelledby={`tab-${active}`} className="pt-5">
        <TabPanel tab={active} canWrite={canWrite} canReadAgents={canReadAgents} />
      </div>
    </main>
  );
}
