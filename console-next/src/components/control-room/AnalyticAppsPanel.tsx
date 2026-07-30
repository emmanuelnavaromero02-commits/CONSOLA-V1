"use client";

import {
  AlertTriangle,
  BarChart3,
  Bot,
  BrainCircuit,
  CheckCircle2,
  Cpu,
  Database,
  Gauge,
  Loader2,
  RefreshCcw,
  ShieldCheck,
  Sparkles,
  Table2,
  TrendingUp,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useMemo, type ReactNode } from "react";

import { OperationalNotice, ReadinessBadge, type ControlRoomStatus } from "./StatusBadge";
import type { AnalyticsApp, AppsResponse } from "@/lib/admin-surfaces";
import type {
  ControlRoomAgentsOpsPayload,
  SfGoldKpisPayload,
  SfGoldWidget,
  SfTalentKpisPayload,
  SfTalentWidget,
  SourceStatus,
} from "@/lib/control-room/types";
import { cn } from "@/lib/utils";

type NativeModuleKind = "successfactors" | "talent" | "agentops" | "generic";

type NativeAnalyticModule = {
  id: string;
  title: string;
  description: string;
  kind: NativeModuleKind;
  app?: AnalyticsApp;
  cartridge?: string;
  status?: string | null;
};

function updated(value?: string | null): string {
  if (!value) return "sin fecha";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace("T", " ");
  return date.toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" });
}

function prettyLabel(value: string): string {
  return value
    .replace(/^sap_successfactors_/, "")
    .replace(/^salesforce_/, "")
    .replace(/^replicon_/, "")
    .replace(/^sap_(hcm|s4hana)_/, "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function appLabel(app: AnalyticsApp): string {
  return app.title || prettyLabel(app.name);
}

function appCartridge(app: AnalyticsApp): string {
  return String(app.cartridge || "").trim();
}

function isSuccessFactorsApp(app: AnalyticsApp): boolean {
  const token = `${app.name} ${app.title || ""} ${app.description || ""} ${appCartridge(app)}`.toLowerCase();
  return token.includes("successfactors") || token.includes("sap_successfactors");
}

function isTalentApp(app: AnalyticsApp): boolean {
  const token = `${app.name} ${app.title || ""} ${app.description || ""}`.toLowerCase();
  return /(talent|readiness|skill|9box|nine|succession|performance|compensation)/.test(token);
}

function moduleStatus(module: NativeAnalyticModule, sources: SourceStatus[]): ControlRoomStatus {
  if (module.status === "ready") return "ready";
  if (module.status === "unready") return "partial";
  if (module.status === "dataset_metadata_missing") return "missing";
  const related = relatedSources(module, sources);
  if (related.some((source) => source.data_readiness === "ready" || source.status === "ok")) return "ready";
  if (related.some((source) => source.data_readiness === "partial" || source.status === "empty")) return "partial";
  if (related.length) return related[0]?.data_readiness || related[0]?.status || "missing";
  return "partial";
}

function buildNativeModules(
  apps: AnalyticsApp[],
  cartridge: string,
  agentsOps: ControlRoomAgentsOpsPayload | null | undefined,
): NativeAnalyticModule[] {
  const modules = apps.map((app): NativeAnalyticModule => ({
    id: app.name,
    title: appLabel(app),
    description: app.description || "Analítica operacional integrada en OMEGA.",
    kind: isSuccessFactorsApp(app) ? (isTalentApp(app) ? "talent" : "successfactors") : "generic",
    app,
    cartridge: appCartridge(app),
    status: app.data_status,
  }));

  const hasSuccessFactors = cartridge === "sap_successfactors" || apps.some(isSuccessFactorsApp);
  if (hasSuccessFactors && !modules.some((module) => module.kind === "successfactors")) {
    modules.unshift({
      id: "omega_successfactors_executive",
      title: "SuccessFactors Ejecutivo",
      description: "Indicadores Gold, cobertura de fuentes y frentes operativos de SuccessFactors.",
      kind: "successfactors",
      cartridge: "sap_successfactors",
      status: "partial",
    });
  }
  if (hasSuccessFactors && !modules.some((module) => module.kind === "talent")) {
    modules.splice(Math.min(1, modules.length), 0, {
      id: "omega_successfactors_talent",
      title: "Talento y Readiness",
      description: "Readiness C/P/A, senales de talento, bloqueos y recomendaciones supervisadas.",
      kind: "talent",
      cartridge: "sap_successfactors",
      status: "partial",
    });
  }
  if (agentsOps && !modules.some((module) => module.kind === "agentops")) {
    modules.push({
      id: "omega_agentops",
      title: "AgentOps y Simulacion",
      description: "Monitores, capacidades, ejecuciones recientes y cobertura de agentes operativos.",
      kind: "agentops",
      status: agentsOps.summary?.active_agents ? "ready" : "partial",
    });
  }
  return modules;
}

function relatedSources(module: NativeAnalyticModule, sources: SourceStatus[]): SourceStatus[] {
  if (module.kind === "agentops") return [];
  const moduleText = [module.id, module.title, module.cartridge]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return sources.filter((source) => {
    const sourceText = [source.cartridge, source.module, source.domain]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    if (module.kind === "successfactors" || module.kind === "talent") {
      return source.cartridge === "sap_successfactors" || sourceText.includes("successfactors");
    }
    return sourceText.length > 0 && (
      moduleText.includes(sourceText) ||
      [source.cartridge, source.module, source.domain].some((value) => (
        typeof value === "string" && value.length > 2 && moduleText.includes(value.toLowerCase())
      ))
    );
  });
}

function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return Number(value).toLocaleString("es-MX", { maximumFractionDigits: Math.abs(Number(value)) >= 100 ? 0 : 1 });
}

function statusCount(sources: SourceStatus[], status: string): number {
  return sources.filter((source) => source.data_readiness === status || source.status === status).length;
}

function agentOpsEngineLabel(engine?: string | null): string {
  const labels: Record<string, string> = {
    wisdom_bit: "WisdomBit",
    monte_carlo: "Análisis operativo",
    bayesian_calibration: "Historial operativo",
    decision_orchestrator: "Decisión",
  };
  return engine ? labels[engine] || engine.replaceAll("_", " ") : "Capacidad operativa";
}

function agentOpsEngineStatusLabel(status?: string | null): string {
  if (status === "ready") return "Listo";
  if (status === "configured") return "En espera de datos";
  if (status === "missing") return "No configurado";
  return status ? status.replaceAll("_", " ") : "En espera";
}

function agentOpsEngineTone(status?: string | null): string {
  if (status === "ready") return "border-emerald-400/30 bg-emerald-500/10 text-emerald-100";
  if (status === "configured") return "border-amber-300/30 bg-amber-500/10 text-amber-100";
  return "border-sky-400/15 bg-slate-950/30 text-slate-300";
}

function widgetRows(widget: SfGoldWidget | SfTalentWidget): Array<{ label: string; value: number }> {
  return (widget.rows || [])
    .map((row) => ({
      label: String(row.label || row.fact || row.status || "Sin etiqueta"),
      value: Number(row.headcount ?? row.value ?? row.count ?? 0),
    }))
    .filter((row) => Number.isFinite(row.value) && row.value > 0)
    .slice(0, 8);
}

function widgetValue(widget: SfGoldWidget | SfTalentWidget): string {
  if (typeof widget.value === "number") return formatNumber(widget.value);
  const rows = widgetRows(widget);
  if (rows.length) return formatNumber(rows.reduce((sum, row) => sum + row.value, 0));
  return "-";
}

export function AnalyticAppsPanel({
  payload,
  loading,
  error,
  selectedApp,
  onSelectedApp,
  onRefresh,
  cartridge,
  sources,
  agentsOps,
  sfGoldKpis,
  sfGoldLoading,
  sfGoldError,
  sfTalentKpis,
  sfTalentLoading,
  sfTalentError,
}: {
  payload: AppsResponse | null;
  loading: boolean;
  error: string;
  selectedApp: string;
  onSelectedApp: (name: string) => void;
  onRefresh: () => void;
  cartridge: string;
  sources: SourceStatus[];
  agentsOps?: ControlRoomAgentsOpsPayload | null;
  sfGoldKpis?: SfGoldKpisPayload | null;
  sfGoldLoading?: boolean;
  sfGoldError?: string;
  sfTalentKpis?: SfTalentKpisPayload | null;
  sfTalentLoading?: boolean;
  sfTalentError?: string;
}) {
  const apps = useMemo(() => payload?.apps ?? [], [payload?.apps]);
  const modules = useMemo(() => buildNativeModules(apps, cartridge, agentsOps), [agentsOps, apps, cartridge]);
  const activeModule = modules.find((module) => module.id === selectedApp) || modules[0] || null;
  const readyCount = modules.filter((module) => moduleStatus(module, sources) === "ready").length;
  const emptyMessage =
    payload?.apps_readiness?.message ||
    payload?.apps_scope?.message ||
    "No hay modulos analiticos publicados para este workspace.";

  return (
    <section className="rounded-xl border bg-card shadow-sm dark:border-sky-400/20 dark:bg-[#081423] dark:shadow-[0_0_30px_rgba(14,165,233,0.10)]" aria-label="Analitica operativa">
      <div className="flex flex-col gap-3 border-b p-4 dark:border-sky-400/15 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">Analitica operativa</p>
          <h2 className="mt-1 text-lg font-semibold text-foreground dark:text-white">Modulos de decision</h2>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Indicadores ejecutivos, cobertura de datos, talento, agentes y seguimiento del frente activo.
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex min-h-[40px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-semibold text-foreground hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300 dark:border-sky-400/20 dark:bg-[#07111e]"
        >
          <RefreshCcw aria-hidden className={cn("h-4 w-4", loading ? "animate-spin" : "")} />
          Actualizar modulos
        </button>
      </div>

      <div className="space-y-4 p-4">
        {error ? <OperationalNotice tone="error" title="No se pudo actualizar el catalogo analitico">{error}</OperationalNotice> : null}
        {loading && !modules.length ? (
          <div className="grid gap-3 md:grid-cols-3">
            {Array.from({ length: 3 }).map((_, index) => (
              <span key={index} aria-hidden className="h-24 animate-pulse rounded-lg bg-muted/60 dark:bg-slate-800/60" />
            ))}
          </div>
        ) : null}
        {!loading && !modules.length ? <OperationalNotice tone="info" title="Sin modulos analiticos">{emptyMessage}</OperationalNotice> : null}

        {modules.length ? (
          <div className="grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
            <aside className="space-y-3" aria-label="Selector de modulos analiticos">
              <div className="rounded-lg border bg-background p-3 dark:border-sky-400/15 dark:bg-[#06111f]">
                <p className="text-xs font-semibold uppercase text-muted-foreground">Modulos disponibles</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {readyCount}/{modules.length} con datos listos.
                </p>
              </div>
              <div className="grid gap-2" role="tablist" aria-label="Seleccionar modulo analitico">
                {modules.map((module) => {
                  const status = moduleStatus(module, sources);
                  return (
                    <button
                      type="button"
                      key={module.id}
                      role="tab"
                      aria-selected={activeModule?.id === module.id}
                      onClick={() => onSelectedApp(module.id)}
                      className={cn(
                        "min-h-[76px] rounded-md border px-3 py-2 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300",
                        activeModule?.id === module.id
                          ? "border-cyan-400 bg-cyan-500/10 text-cyan-700 shadow-sm dark:text-cyan-200"
                          : "border-slate-200 bg-background text-muted-foreground hover:text-foreground dark:border-sky-400/15 dark:bg-[#07111e]",
                      )}
                    >
                      <span className="flex min-w-0 items-center gap-2">
                        <ModuleIcon kind={module.kind} className="h-4 w-4 shrink-0" />
                        <span className="block min-w-0 truncate font-semibold">{module.title}</span>
                      </span>
                      <span className="mt-1 block truncate text-xs opacity-75">
                        {relatedSources(module, sources).length
                          ? `${relatedSources(module, sources).length} fuentes visibles`
                          : "datos de Control Room"}
                      </span>
                      <span className="mt-2 flex flex-wrap items-center gap-2">
                        <ReadinessBadge status={status} compact />
                        {module.app?.updated_at ? <span className="text-xs opacity-60">{updated(module.app.updated_at)}</span> : null}
                      </span>
                    </button>
                  );
                })}
              </div>
            </aside>

            {activeModule ? (
              <NativeModuleShell module={activeModule} sources={sources}>
                {activeModule.kind === "successfactors" ? (
                  <SuccessFactorsNativeModule
                    module={activeModule}
                    sources={sources}
                    payload={sfGoldKpis}
                    loading={Boolean(sfGoldLoading)}
                    error={sfGoldError || ""}
                    talent={sfTalentKpis}
                  />
                ) : null}
                {activeModule.kind === "talent" ? (
                  <TalentNativeModule
                    module={activeModule}
                    sources={sources}
                    payload={sfTalentKpis}
                    loading={Boolean(sfTalentLoading)}
                    error={sfTalentError || ""}
                  />
                ) : null}
                {activeModule.kind === "agentops" ? (
                  <AgentOpsNativeModule payload={agentsOps} />
                ) : null}
                {activeModule.kind === "generic" ? (
                  <GenericNativeModule module={activeModule} sources={sources} />
                ) : null}
              </NativeModuleShell>
            ) : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}

function ModuleIcon({ kind, className }: { kind: NativeModuleKind; className?: string }) {
  if (kind === "talent") return <Users aria-hidden className={className} />;
  if (kind === "agentops") return <Bot aria-hidden className={className} />;
  if (kind === "successfactors") return <TrendingUp aria-hidden className={className} />;
  return <BarChart3 aria-hidden className={className} />;
}

function NativeModuleShell({
  module,
  sources,
  children,
}: {
  module: NativeAnalyticModule;
  sources: SourceStatus[];
  children: ReactNode;
}) {
  const status = moduleStatus(module, sources);
  return (
    <article className="min-w-0 overflow-hidden rounded-lg border bg-background dark:border-sky-400/15 dark:bg-[#06111f]">
      <header className="flex flex-col gap-3 border-b px-4 py-4 dark:border-sky-400/15 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <ModuleIcon kind={module.kind} className="h-5 w-5 text-cyan-600 dark:text-cyan-300" />
            <h3 className="truncate text-lg font-semibold text-foreground dark:text-white">{module.title}</h3>
            <ReadinessBadge status={status} compact />
          </div>
          <p className="mt-2 max-w-4xl text-sm text-muted-foreground">{module.description}</p>
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs sm:min-w-[220px]">
          <MiniStat label="Fuentes" value={formatNumber(relatedSources(module, sources).length)} />
          <MiniStat label="Estado" value={prettyLabel(status)} />
        </div>
      </header>
      <div className="bg-[#07111e] p-4">{children}</div>
    </article>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-card p-3 dark:border-sky-400/15 dark:bg-[#07111e]">
      <p className="text-[11px] font-semibold uppercase text-muted-foreground">{label}</p>
      <strong className="mt-1 block text-base font-semibold text-foreground dark:text-white">{value}</strong>
    </div>
  );
}

function SuccessFactorsNativeModule({
  module,
  sources,
  payload,
  loading,
  error,
  talent,
}: {
  module: NativeAnalyticModule;
  sources: SourceStatus[];
  payload?: SfGoldKpisPayload | null;
  loading: boolean;
  error: string;
  talent?: SfTalentKpisPayload | null;
}) {
  const related = relatedSources(module, sources);
  const widgets = payload?.widgets || [];
  const topWidgets = widgets.filter((widget) => typeof widget.value === "number" || widgetRows(widget).length).slice(0, 8);
  return (
    <div className="space-y-4">
      {loading ? <LoadingNotice label="Actualizando indicadores SuccessFactors..." /> : null}
      {error ? <OperationalNotice tone="warning" title="Indicadores ejecutivos no disponibles">No se pudo actualizar esta vista.</OperationalNotice> : null}
      <div className="grid gap-3 md:grid-cols-4">
        <MetricCard icon={Users} label="Widgets Gold" value={formatNumber(widgets.length)} />
        <MetricCard icon={Database} label="Fuentes listas" value={formatNumber(statusCount(related, "ready") + statusCount(related, "ok"))} />
        <MetricCard icon={AlertTriangle} label="Parciales" value={formatNumber(statusCount(related, "partial"))} tone="warning" />
        <MetricCard icon={BrainCircuit} label="Talento" value={talent?.readiness?.status ? prettyLabel(String(talent.readiness.status)) : "pendiente"} />
      </div>
      {topWidgets.length ? (
        <div className="grid gap-4 xl:grid-cols-2">
          {topWidgets.map((widget, index) => <WidgetPanel key={`${widget.id || widget.title || "widget"}:${index}`} widget={widget} />)}
        </div>
      ) : (
        <OperationalNotice tone="info" title="Modulo listo">
          Control Room esta integrado; al materializar Gold se llenan los paneles ejecutivos sin abrir pantallas externas.
        </OperationalNotice>
      )}
      <SourceReadinessGrid sources={related} />
    </div>
  );
}

function TalentNativeModule({
  module,
  sources,
  payload,
  loading,
  error,
}: {
  module: NativeAnalyticModule;
  sources: SourceStatus[];
  payload?: SfTalentKpisPayload | null;
  loading: boolean;
  error: string;
}) {
  const related = relatedSources(module, sources);
  const widgets = payload?.widgets || [];
  const blockers = payload?.blockers || [];
  const signals = payload?.signals || [];
  return (
    <div className="space-y-4">
      {loading ? <LoadingNotice label="Actualizando Talento..." /> : null}
      {error ? <OperationalNotice tone="warning" title="Talento no disponible">No se pudo actualizar esta vista.</OperationalNotice> : null}
      <div className="grid gap-3 md:grid-cols-4">
        <MetricCard icon={ShieldCheck} label="Ready C/P/A" value={formatNumber(payload?.readiness?.ready_min)} />
        <MetricCard icon={Users} label="Perfilados" value={formatNumber(payload?.readiness?.profiled_employees)} />
        <MetricCard icon={AlertTriangle} label="Insuficientes" value={formatNumber(payload?.readiness?.insufficient_data_employees)} tone="warning" />
        <MetricCard icon={Sparkles} label="Senales" value={formatNumber(signals.length)} />
      </div>
      {widgets.length ? (
        <div className="grid gap-4 xl:grid-cols-2">
          {widgets.slice(0, 6).map((widget, index) => <WidgetPanel key={`${widget.id || widget.title || "widget"}:${index}`} widget={widget} />)}
        </div>
      ) : null}
      <div className="grid gap-4 xl:grid-cols-2">
        <section className="rounded-lg border border-sky-400/15 bg-[#06111f] p-4">
          <div className="mb-3 flex items-center gap-2">
            <BrainCircuit aria-hidden className="h-4 w-4 text-cyan-300" />
            <h4 className="text-sm font-semibold text-white">Senales y recomendacion</h4>
          </div>
          <div className="space-y-2">
            {signals.slice(0, 5).map((signal, index) => (
              <div key={`${signal.id || signal.title || "signal"}:${index}`} className="rounded-md border border-sky-400/10 bg-slate-950/30 p-3">
                <p className="text-sm font-semibold text-slate-100">{signal.title || "Señal de talento"}</p>
                <p className="mt-1 text-xs text-slate-400">{signal.recommendation || "Pendiente de recomendacion supervisada."}</p>
              </div>
            ))}
            {!signals.length ? <p className="text-sm text-slate-400">Sin senales listas para este workspace.</p> : null}
          </div>
        </section>
        <section className="rounded-lg border border-sky-400/15 bg-[#06111f] p-4">
          <div className="mb-3 flex items-center gap-2">
            <AlertTriangle aria-hidden className="h-4 w-4 text-amber-300" />
            <h4 className="text-sm font-semibold text-white">Bloqueos de datos</h4>
          </div>
          <div className="space-y-2">
            {blockers.slice(0, 5).map((blocker, index) => (
              <div key={`${blocker.id || blocker.title || "blocker"}:${index}`} className="rounded-md border border-amber-300/20 bg-amber-500/10 p-3">
                <p className="text-sm font-semibold text-amber-100">{blocker.title || "Cobertura pendiente"}</p>
                <ReadinessBadge status={blocker.status || "blocked"} compact />
              </div>
            ))}
            {!blockers.length ? <p className="text-sm text-slate-400">Sin bloqueos reportados.</p> : null}
          </div>
        </section>
      </div>
      <SourceReadinessGrid sources={related} />
    </div>
  );
}

function AgentOpsNativeModule({ payload }: { payload?: ControlRoomAgentsOpsPayload | null }) {
  const agents = payload?.agents || [];
  const runs = payload?.recent_runs || [];
  const engines = payload?.engines || [];
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-4">
        <MetricCard icon={Bot} label="Agentes" value={formatNumber(payload?.summary?.agents_total)} />
        <MetricCard icon={CheckCircle2} label="Activos" value={formatNumber(payload?.summary?.active_agents)} />
        <MetricCard icon={ShieldCheck} label="Monitores" value={formatNumber(payload?.summary?.monitor_agents)} />
        <MetricCard icon={AlertTriangle} label="Alertas abiertas" value={formatNumber(payload?.summary?.open_agent_alerts)} tone="warning" />
      </div>
      <div className="grid gap-3 md:grid-cols-4">
        <MetricCard icon={BrainCircuit} label="Capacidades" value={formatNumber(payload?.summary?.configured_engines)} />
        <MetricCard icon={Gauge} label="Análisis operativo" value={formatNumber(payload?.summary?.monte_carlo_simulations)} />
        <MetricCard icon={BrainCircuit} label="Historial operativo" value={formatNumber(payload?.summary?.bayesian_calibration_samples)} />
        <MetricCard icon={Cpu} label="Decisión" value={formatNumber(payload?.summary?.decision_orchestrations)} />
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <section className="rounded-lg border border-sky-400/15 bg-[#06111f] p-4">
          <h4 className="text-sm font-semibold text-white">Agentes conectados</h4>
          <div className="mt-3 space-y-2">
            {agents.slice(0, 8).map((agent, index) => (
              <div key={`${agent.name || "agent"}:${index}`} className="grid gap-2 rounded-md border border-sky-400/10 bg-slate-950/30 p-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-slate-100">{agent.name || "Agente"}</p>
                  <p className="mt-1 text-xs text-slate-400">{agent.operational_tools_count} capacidades operativas</p>
                </div>
                <ReadinessBadge status={agent.operationally_ready ? "ready" : "partial"} compact />
              </div>
            ))}
            {!agents.length ? <p className="text-sm text-slate-400">AgentOps todavia no reporta agentes para este workspace.</p> : null}
          </div>
        </section>
        <div className="space-y-4">
          <section className="rounded-lg border border-sky-400/15 bg-[#06111f] p-4">
            <h4 className="text-sm font-semibold text-white">Capacidades de análisis</h4>
            <div className="mt-3 space-y-2">
              {engines.map((engine, index) => (
                <div key={`${engine.engine || "engine"}:${index}`} className="rounded-md border border-sky-400/10 bg-slate-950/30 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-semibold text-slate-100">{agentOpsEngineLabel(engine.engine)}</p>
                    <span className={cn("rounded-md border px-2 py-0.5 text-xs font-semibold", agentOpsEngineTone(engine.status))}>
                      {agentOpsEngineStatusLabel(engine.status)}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-slate-400">
                    {formatNumber(engine.configured)} configuradas · {formatNumber(engine.sample_count ?? engine.evidence_count)} evidencia
                  </p>
                </div>
              ))}
              {!engines.length ? <p className="text-sm text-slate-400">Sin capacidades declaradas por los monitores.</p> : null}
            </div>
          </section>
          <section className="rounded-lg border border-sky-400/15 bg-[#06111f] p-4">
            <h4 className="text-sm font-semibold text-white">Ejecuciones recientes</h4>
            <div className="mt-3 space-y-3">
              {runs.slice(0, 5).map((run, index) => (
                <div key={`${run.agent_name || "run"}:${run.started_at || run.finished_at || index}`} className="rounded-md border border-sky-400/10 bg-slate-950/30 p-3">
                  <p className="text-sm font-semibold text-slate-100">{run.agent_name || "Agente"}</p>
                  <p className="mt-1 text-xs text-slate-400">{run.status || "sin estado"} · {run.tool_count} capacidades · {run.started_at ? updated(run.started_at) : "sin fecha"}</p>
                </div>
              ))}
              {!runs.length ? <p className="text-sm text-slate-400">Sin ejecuciones recientes persistidas.</p> : null}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

function GenericNativeModule({ module, sources }: { module: NativeAnalyticModule; sources: SourceStatus[] }) {
  const related = relatedSources(module, sources);
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-4">
        <MetricCard icon={Database} label="Fuentes vinculadas" value={formatNumber(related.length)} />
        <MetricCard icon={CheckCircle2} label="Listas" value={formatNumber(statusCount(related, "ready") + statusCount(related, "ok"))} />
        <MetricCard icon={AlertTriangle} label="Parciales" value={formatNumber(statusCount(related, "partial"))} tone="warning" />
        <MetricCard icon={Table2} label="Registros visibles" value={formatNumber(related.reduce((sum, source) => sum + (source.count || 0), 0))} />
      </div>
      {related.length ? (
        <SourceReadinessGrid sources={related} />
      ) : (
        <OperationalNotice tone="info" title="Modulo interno preparado">
          Este modulo ya esta disponible en Control Room. Las fuentes visibles aparecerán cuando haya información operativa disponible.
        </OperationalNotice>
      )}
    </div>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  tone = "normal",
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  tone?: "normal" | "warning";
}) {
  return (
    <div className={cn(
      "rounded-lg border bg-[#06111f] p-4",
      tone === "warning" ? "border-amber-300/30" : "border-sky-400/15",
    )}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase text-slate-400">{label}</p>
        <Icon aria-hidden className={cn("h-4 w-4", tone === "warning" ? "text-amber-300" : "text-cyan-300")} />
      </div>
      <strong className={cn("mt-2 block text-2xl font-semibold", tone === "warning" ? "text-amber-100" : "text-white")}>{value}</strong>
    </div>
  );
}

function LoadingNotice({ label }: { label: string }) {
  return (
    <div className="flex min-h-[60px] items-center gap-2 rounded-lg border border-sky-400/15 bg-[#06111f] px-4 text-sm text-slate-300">
      <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
      {label}
    </div>
  );
}

function WidgetPanel({ widget }: { widget: SfGoldWidget | SfTalentWidget }) {
  const rows = widgetRows(widget);
  const max = Math.max(1, ...rows.map((row) => row.value));
  const sourceLabel = "Indicador ejecutivo";
  return (
    <section className="rounded-lg border border-sky-400/15 bg-[#06111f] p-4">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase text-cyan-300/80">{sourceLabel}</p>
          <h4 className="text-base font-semibold text-white">{widget.title || "Indicador"}</h4>
        </div>
        <strong className="text-2xl font-semibold text-white">{widgetValue(widget)}</strong>
      </div>
      {rows.length ? (
        <div className="space-y-3">
          {rows.map((row) => (
            <div key={row.label} className="grid gap-2 sm:grid-cols-[150px_minmax(0,1fr)_70px] sm:items-center">
              <span className="truncate text-sm text-slate-300">{row.label}</span>
              <span className="h-7 overflow-hidden rounded-md bg-slate-900">
                <span className="block h-full rounded-md bg-cyan-400/70" style={{ width: `${Math.max(4, Math.min(100, (row.value / max) * 100))}%` }} />
              </span>
              <span className="text-right text-sm font-semibold text-white">{formatNumber(row.value)}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-slate-400">Sin desglose disponible todavia.</p>
      )}
    </section>
  );
}

function SourceReadinessGrid({ sources }: { sources: SourceStatus[] }) {
  if (!sources.length) return null;
  return (
    <section className="rounded-lg border border-sky-400/15 bg-[#06111f] p-4">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase text-cyan-300/80">Fuentes y cobertura</p>
          <h4 className="text-base font-semibold text-white">Estado operacional</h4>
        </div>
        <span className="text-xs text-slate-400">{sources.length} fuentes</span>
      </div>
      <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
        {sources.slice(0, 12).map((source, index) => (
          <div key={`${source.cartridge || source.module || source.domain || "source"}:${index}`} className="rounded-md border border-sky-400/10 bg-slate-950/30 p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-slate-100">{source.module || source.domain || "Fuente operativa"}</p>
                <p className="mt-1 truncate text-xs text-slate-400">{source.domain || source.cartridge || "Cobertura de Control Room"}</p>
              </div>
              <ReadinessBadge status={source.data_readiness || source.status || "missing"} compact />
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-400">
              <span>{formatNumber(source.count)} rows</span>
              <span className="truncate text-right">{source.checked_at ? updated(source.checked_at) : "sin revision"}</span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
