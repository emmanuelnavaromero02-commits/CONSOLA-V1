"use client";

import {
  AlertTriangle,
  Bot,
  BrainCircuit,
  ChevronDown,
  ChevronRight,
  Clock3,
  Cpu,
  Gauge,
  ShieldCheck,
  Wrench,
} from "lucide-react";

import { CommandMetric, MiniBar, OperationalNotice } from "./StatusBadge";
import type { ControlRoomAgentsOpsEngine, ControlRoomAgentsOpsPayload } from "@/lib/control-room/types";
import { cn } from "@/lib/utils";

function labelForOrigin(origin?: string | null): string {
  const key = (origin || "").replaceAll("_", " ").trim();
  if (!key || key === "unknown") return "Sin origen";
  return key.replace(/\b\w/g, (char) => char.toUpperCase());
}

function statusTone(status?: string | null): string {
  if (status === "ok") return "border-emerald-400/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200";
  if (status === "error" || status === "cancelled") return "border-rose-400/30 bg-rose-500/10 text-rose-700 dark:text-rose-200";
  if (status === "running") return "border-amber-400/30 bg-amber-500/10 text-amber-700 dark:text-amber-200";
  return "border-slate-300 bg-slate-100 text-slate-600 dark:border-sky-400/20 dark:bg-slate-900/60 dark:text-slate-300";
}

function engineLabel(engine?: string | null): string {
  const labels: Record<string, string> = {
    wisdom_bit: "WisdomBit",
    monte_carlo: "Análisis operativo",
    bayesian_calibration: "Historial operativo",
    decision_orchestrator: "Decisión",
  };
  return engine ? labels[engine] || engine.replaceAll("_", " ") : "Capacidad operativa";
}

function engineStatusLabel(status?: string | null): string {
  if (status === "ready") return "Listo";
  if (status === "configured") return "En espera de datos";
  if (status === "missing") return "No configurado";
  return status ? status.replaceAll("_", " ") : "En espera";
}

function engineStatusTone(status?: string | null): string {
  if (status === "ready") return "border-emerald-400/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200";
  if (status === "configured") return "border-amber-400/30 bg-amber-500/10 text-amber-700 dark:text-amber-200";
  return "border-slate-300 bg-slate-100 text-slate-600 dark:border-sky-400/20 dark:bg-slate-900/60 dark:text-slate-300";
}

function engineDetail(engine: ControlRoomAgentsOpsEngine | undefined, ready: string, configured: string, missing: string): string {
  if (!engine) return missing;
  if (engine.status === "ready") return ready;
  if (engine.status === "configured") return configured;
  return missing;
}

// Sin payload (carga o error) no se fabrican ceros: se muestra "—".
function metricValue(loading: boolean, value: number | null | undefined): string | number {
  if (loading) return "...";
  return typeof value === "number" ? value : "—";
}

function metricDetail(available: boolean, text: string): string {
  return available ? text : "—";
}

export function AgentsOpsPanel({
  payload,
  loading,
  error,
  collapsed,
  onToggle,
}: {
  payload: ControlRoomAgentsOpsPayload | null;
  loading: boolean;
  error: string;
  collapsed: boolean;
  onToggle: () => void;
}) {
  const summary = payload?.summary;
  const topOrigin = payload?.origins?.[0];
  const originMax = Math.max(1, ...(payload?.origins?.map((item) => item.count) ?? [1]));
  const agents = payload?.agents ?? [];
  const diagnostics = payload?.operational_diagnostics ?? [];
  const monitorAgents = agents.filter((agent) => agent.monitor);
  const operationalMonitors = monitorAgents.filter((agent) => agent.operationally_ready);
  const engines = payload?.engines ?? [];
  const monteCarloEngine = engines.find((engine) => engine.engine === "monte_carlo");
  const bayesEngine = engines.find((engine) => engine.engine === "bayesian_calibration");

  return (
    <section className="border-y bg-transparent dark:border-sky-400/20" aria-label="Agentes e inteligencia">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300"
      >
        <span>
          <span className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">AgentOps</span>
      <span className="mt-1 block text-lg font-semibold text-foreground dark:text-white">Agentes, WisdomBits y análisis</span>
        </span>
        <span className="flex items-center gap-2 text-sm text-muted-foreground">
          {summary ? `${operationalMonitors.length}/${summary.monitor_agents} monitores operativos · ${summary.open_agent_alerts} alertas` : loading ? "cargando" : "sin datos"}
          {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </span>
      </button>

      {collapsed ? null : (
        <div className="space-y-4 border-t px-4 py-4 dark:border-sky-400/15">
          {error ? <OperationalNotice tone="error" title="Agentes no disponibles">No se pudo actualizar esta vista.</OperationalNotice> : null}
          {payload?.generated_at ? (
            <p className="text-xs text-muted-foreground">
              Actualizado: <time dateTime={payload.generated_at}>{new Date(payload.generated_at).toLocaleString("es-MX")}</time>
            </p>
          ) : null}
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <CommandMetric icon={Bot} label="Agentes activos" value={metricValue(loading, summary?.active_agents)} detail={metricDetail(Boolean(summary), `${summary?.agents_total ?? 0} registrados`)} />
            <CommandMetric icon={ShieldCheck} label="Monitores operativos" value={loading ? "..." : summary ? operationalMonitors.length : "—"} detail={metricDetail(Boolean(summary), `${summary?.monitor_agents ?? 0} con contrato`)} />
            <CommandMetric icon={Cpu} label="Runs recientes" value={metricValue(loading, summary?.recent_runs)} detail={metricDetail(Boolean(summary), `${summary?.failed_recent_runs ?? 0} con error`)} />
            <CommandMetric icon={AlertTriangle} label="Alertas agente" value={metricValue(loading, summary?.open_agent_alerts)} detail={metricDetail(Boolean(summary), `${summary?.agent_alerts_total ?? 0} históricas`)} />
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <CommandMetric icon={BrainCircuit} label="Capacidades configuradas" value={metricValue(loading, summary?.configured_engines)} detail={metricDetail(Boolean(summary), `${engines.filter((engine) => engine.status === "ready").length} con evidencia`)} />
            <CommandMetric icon={Gauge} label="Análisis operativo" value={metricValue(loading, summary?.monte_carlo_simulations)} detail={engineDetail(monteCarloEngine, "resultados persistidos", "en espera de datos", "sin análisis configurado")} />
            <CommandMetric icon={BrainCircuit} label="Historial operativo" value={metricValue(loading, summary?.bayesian_calibration_samples)} detail={engineDetail(bayesEngine, `${summary?.bayesian_calibration_states ?? 0} estados con historial`, "requiere historial adicional", "sin historial configurado")} />
            <CommandMetric icon={Cpu} label="Decisión" value={metricValue(loading, summary?.decision_orchestrations)} detail={metricDetail(Boolean(summary), "orquestaciones guardadas")} />
          </div>
          {!loading && monteCarloEngine?.status === "configured" ? (
            <OperationalNotice tone="warning" title="Análisis configurado sin evidencia">
              La capacidad existe, pero todavía no hay resultados persistidos para este workspace.
            </OperationalNotice>
          ) : null}
          {!loading && bayesEngine?.status === "configured" ? (
            <OperationalNotice tone="warning" title="Historial operativo insuficiente">
              La capacidad existe, pero falta registrar resultados observados para ajustar probabilidades.
            </OperationalNotice>
          ) : null}
          {!loading && summary?.monitor_agents === 0 ? (
            <OperationalNotice tone="warning" title="Sin monitor operativo">
              No hay agentes de monitor workspace-scoped para ejecutar WisdomBits, análisis o Decision desde Control Room.
            </OperationalNotice>
          ) : null}
          {!loading && summary && summary.monitor_agents > 0 && operationalMonitors.length === 0 ? (
            <OperationalNotice tone="warning" title="Monitores sin tools operativas">
              Hay monitores registrados, pero no tienen tools AgentOps disponibles en el catálogo vivo.
            </OperationalNotice>
          ) : null}

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(300px,0.6fr)]">
            <div className="space-y-3">
              {agents.slice(0, 8).map((agent, index) => (
                <article key={`${agent.name || "agent"}:${index}`} className="rounded-lg border bg-background p-3 dark:border-sky-400/15 dark:bg-[#06111f]">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="truncate text-sm font-semibold text-foreground dark:text-white">{agent.name || "Agente"}</h3>
                        <span className={cn("rounded-full border px-2 py-0.5 text-xs font-medium", statusTone(agent.last_run?.status))}>
                          {agent.last_run?.status || "sin run"}
                        </span>
                        <span className={cn(
                          "rounded-full border px-2 py-0.5 text-xs font-medium",
                          agent.operationally_ready
                            ? "border-emerald-400/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200"
                            : agent.monitor
                              ? "border-amber-400/30 bg-amber-500/10 text-amber-700 dark:text-amber-200"
                              : "border-slate-300 bg-slate-100 text-slate-600 dark:border-sky-400/20 dark:bg-slate-900/60 dark:text-slate-300",
                        )}>
                          {agent.operationally_ready ? "monitor operativo" : agent.monitor ? "monitor incompleto" : "agente"}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {agent.role || "Rol operativo"}
                      </p>
                    </div>
                    <div className="text-right text-xs text-muted-foreground">
                      <div>{agent.alerts.open} abiertas</div>
                      <div>{agent.alerts.total} alertas</div>
                    </div>
                  </div>
                  <div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
                    <span className="inline-flex items-center gap-1"><Clock3 className="h-3.5 w-3.5" /> {agent.last_run?.started_at ? new Date(agent.last_run.started_at).toLocaleString("es-MX") : "sin ejecución"}</span>
                    <span className="truncate">{agent.last_run?.finished_at ? `Finalizó ${new Date(agent.last_run.finished_at).toLocaleString("es-MX")}` : "sin cierre registrado"}</span>
                    <span className="inline-flex items-center gap-1 truncate"><Wrench className="h-3.5 w-3.5" /> {agent.operational_tools_count} capacidades operativas</span>
                  </div>
                </article>
              ))}
              {!loading && agents.length === 0 ? (
                <OperationalNotice tone="warning" title="Sin agentes visibles">No hay agentes workspace-scoped o globales para este contexto.</OperationalNotice>
              ) : null}
            </div>

            <div className="space-y-3">
              <div className="rounded-lg border bg-background p-3 dark:border-sky-400/15 dark:bg-[#06111f]">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-foreground dark:text-white">Capacidades</h3>
                  <span className="text-xs text-muted-foreground">{engines.length}</span>
                </div>
                <div className="space-y-2">
                  {engines.map((engine, index) => (
                    <div key={`${engine.engine || "engine"}:${index}`} className="rounded-md border bg-muted/20 p-2 text-xs dark:border-sky-400/10 dark:bg-slate-950/30">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium text-foreground dark:text-white">{engineLabel(engine.engine)}</span>
                        <span className={cn("rounded-full border px-2 py-0.5 font-medium", engineStatusTone(engine.status))}>{engineStatusLabel(engine.status)}</span>
                      </div>
                      <div className="mt-1 flex items-center justify-between gap-2 text-muted-foreground">
                        <span>{engine.configured} configuradas</span>
                        <span>{engine.sample_count ?? engine.evidence_count} evidencia</span>
                      </div>
                    </div>
                  ))}
                  {!loading && engines.length === 0 ? (
                    <p className="text-xs text-muted-foreground">Sin capacidades registradas.</p>
                  ) : null}
                </div>
              </div>

              <div className="rounded-lg border bg-background p-3 dark:border-sky-400/15 dark:bg-[#06111f]">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-foreground dark:text-white">Origen de señales</h3>
                  <span className="text-xs text-muted-foreground">{payload?.origins?.length ?? 0}</span>
                </div>
                <MiniBar
                  value={topOrigin?.count ?? 0}
                  max={originMax}
                  label={topOrigin ? labelForOrigin(topOrigin.origin) : "sin señales"}
                  tone={summary?.open_agent_alerts ? "warning" : "neutral"}
                />
                <div className="mt-3 space-y-2">
                  {(payload?.origins ?? []).slice(0, 6).map((item) => (
                    <div key={item.origin} className="flex items-center justify-between gap-2 text-xs">
                      <span className="truncate text-muted-foreground">{labelForOrigin(item.origin)}</span>
                      <span className="font-semibold text-foreground dark:text-white">{item.count}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="rounded-lg border bg-background p-3 dark:border-sky-400/15 dark:bg-[#06111f]">
                <h3 className="mb-2 text-sm font-semibold text-foreground dark:text-white">Diagnóstico operativo</h3>
                <div className="space-y-2">
                  {diagnostics.slice(0, 6).map((item, index) => (
                    <div key={`${item.diagnostic || "diagnostic"}:${index}`} className="flex items-center justify-between gap-2 text-xs">
                      <span className="truncate text-muted-foreground">{item.diagnostic || item.scope || "Estado operativo"}</span>
                      <span className="font-semibold text-foreground dark:text-white">{item.sample_count || item.state_count}</span>
                    </div>
                  ))}
                  {!loading && diagnostics.length === 0 ? (
                    <p className="text-xs text-muted-foreground">Sin diagnóstico operativo reciente.</p>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
