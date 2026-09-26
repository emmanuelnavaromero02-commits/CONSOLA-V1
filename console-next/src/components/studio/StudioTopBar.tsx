"use client";

import { Rocket, Sparkles } from "lucide-react";
import type { ReactNode } from "react";

import { formatRelativeFromNow } from "@/lib/control-room/experience-presenter";
import { useDatasets } from "@/lib/monitor/hooks";
import { absoluteTime, formatCount } from "@/lib/studio/format";
import { cartridgeHealth } from "@/lib/studio/health";
import type { StudioCartridgeSummary, StudioManifest } from "@/lib/studio/types";
import { cn } from "@/lib/utils";

import { CartridgeBar } from "./CartridgeBar";
import { buttonClass, primaryButtonClass } from "./ui";

function Metric({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <div data-metric={id} className="min-w-[120px] rounded-lg border bg-background/60 px-3 py-2">
      <dt className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-sm font-semibold tabular-nums">{children}</dd>
    </div>
  );
}

export function StudioHealth({
  cartridge,
  manifest,
  summary,
}: {
  cartridge: string | null;
  manifest: StudioManifest | null | undefined;
  summary: StudioCartridgeSummary | null | undefined;
}) {
  const datasets = useDatasets();
  const loaded = datasets.isSuccess && Array.isArray(datasets.data);
  const health = cartridgeHealth({ cartridge, manifest, summary, datasets: loaded ? datasets.data : null });
  if (!cartridge) return null;
  return (
    <dl aria-label="Salud del cartucho" data-testid="studio-health" className="flex flex-wrap gap-2">
      {health.tables !== null ? (
        <Metric id="tables" label="Total tablas">
          {formatCount(health.tables)}
        </Metric>
      ) : null}
      {loaded && health.goldTotal !== null ? (
        <Metric id="gold-ready" label="Datasets listos">
          {health.goldTotal === 0
            ? "Sin datasets Oro"
            : `${formatCount(health.goldReady ?? 0)} de ${formatCount(health.goldTotal)}`}
        </Metric>
      ) : null}
      {health.lastRefresh ? (
        <Metric id="last-refresh" label="Último refresco">
          <time dateTime={health.lastRefresh} title={absoluteTime(health.lastRefresh)}>
            {formatRelativeFromNow(health.lastRefresh)}
          </time>
        </Metric>
      ) : null}
    </dl>
  );
}

export function StudioTopBar({
  cartridges,
  loading,
  error,
  onRetry,
  activeId,
  onSelect,
  manifest,
  assistantOpen,
  onToggleAssistant,
  onDeploy,
}: {
  cartridges: StudioCartridgeSummary[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  activeId: string | null;
  onSelect: (id: string) => void;
  manifest: StudioManifest | null | undefined;
  assistantOpen: boolean;
  onToggleAssistant: () => void;
  onDeploy: () => void;
}) {
  const summary = cartridges.find((item) => item.id === activeId) ?? null;
  return (
    <header className="space-y-3 rounded-xl border bg-card p-4 shadow-sm">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <h1 className="text-3xl font-semibold tracking-tight">Studio</h1>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className={primaryButtonClass}
            onClick={onDeploy}
            disabled={!activeId}
            aria-describedby="studio-deploy-hint"
          >
            <Rocket aria-hidden className="h-4 w-4" /> Desplegar a Airflow
          </button>
          <span id="studio-deploy-hint" className="sr-only">
            Abre Automatizaciones para elegir el DAG y confirmar el despliegue.
          </span>
          <button
            type="button"
            className={cn(
              buttonClass,
              "border-primary/50 bg-primary/10 text-primary hover:bg-primary/15",
              assistantOpen && "border-primary bg-primary text-primary-foreground hover:bg-primary/90",
            )}
            aria-expanded={assistantOpen}
            aria-controls="studio-assistant-region"
            onClick={onToggleAssistant}
          >
            <Sparkles aria-hidden className="h-4 w-4" /> Consultar al Asistente de Studio
          </button>
        </div>
      </div>
      <CartridgeBar
        cartridges={cartridges}
        loading={loading}
        error={error}
        onRetry={onRetry}
        activeId={activeId}
        onSelect={onSelect}
      />
      <StudioHealth cartridge={activeId} manifest={manifest} summary={summary} />
    </header>
  );
}
