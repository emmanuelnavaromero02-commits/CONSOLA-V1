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

function labelForOrigin(origin: string): string {
  const key = origin.replaceAll("_", " ").trim();
  if (!key || key === "unknown") return "Sin origen";
  return key.replace(/\b\w/g, (char) => char.toUpperCase());
}

function cronLabel(schedule?: Record<string, unknown>): string {
  const cron = typeof schedule?.cron === "string" ? schedule.cron : "";
  const every = typeof schedule?.every === "string" ? schedule.every : "";
  return cron || every || "sin agenda";
}

function monitorDataset(agent: ControlRoomAgentsOpsPayload["agents"][number]): string {
  const contract = agent.monitor_contract || {};
  const dataset = contract.dataset || contract.source_dataset || contract.source;
  return typeof dataset === "string" && dataset.trim() ? dataset : agent.cartridge_id;
}

function statusTone(status?: string | null): string {
  if (status === "ok") return "border-emerald-400/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200";
  if (status === "error" || status === "cancelled") return "border-rose-400/30 bg-rose-500/10 text-rose-700 dark:text-rose-200";
  if (status === "running") return "border-amber-400/30 bg-amber-500/10 text-amber-700 dark:text-amber-200";
  return "border-slate-300 bg-slate-100 text-slate-600 dark:border-sky-400/20 dark:bg-slate-900/60 dark:text-slate-300";
}

function engineLabel(engine: string): string {
  const labels: Record<string, string> = {
    wisdom_bit: "WisdomBit",
    monte_carlo: "Monte Carlo",
    bayesian_calibration: "Bayes",
    decision_orchestrator: "Decision",
  };
  return labels[engine] || engine.replaceAll("_", " ");
}

function engineStatusTone(status?: string): string {
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
  const topOrigin = payload?.origins[0];
  const originMax = Math.max(1, ...(payload?.origins.map((item) => item.count) ?? [1]));
  const agents = payload?.agents ?? [];
  const monitorAgents = agents.filter((agent) => agent.monitor);
  const operationalMonitors = monitorAgents.filter((agent) => agent.operationally_ready);
  const engines = payload?.engines ?? [];
  const monteCarloEngine = engines.find((engine) => engine.engine === "monte_carlo");
  const bayesEngine = engines.find((engine) => engine.engine === "bayesian_calibration");

  return (
    <section className="border-y bg-transparent dark:border-sky-400/20" aria-label="Agentes y simulaciones">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300"
      >
        <span>
          <span className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">AgentOps</span>
          <span className="mt-1 block text-lg font-semibold text-foreground dark:text-white">Agentes, WisdomBits y simulaciones</span>
        </span>
        <span className="flex items-center gap-2 text-sm text-muted-foreground">
          {summary ? `${operationalMonitors.length}/${summary.monitor_agents} monitores operativos · ${summary.open_agent_alerts} alertas` : loading ? "cargando" : "sin datos"}
          {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </span>
      </button>

      {collapsed ? null : (
        <div className="space-y-4 border-t px-4 py-4 dark:border-sky-400/15">
          {error ? <OperationalNotice tone="error" title="Agentes no disponibles">{error}</OperationalNotice> : null}
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <CommandMetric icon={Bot} label="Agentes activos" value={loading ? "..." : summary?.active_agents ?? 0} detail={`${summary?.agents_total ?? 0} registrados`} />
            <CommandMetric icon={ShieldCheck} label="Monitores operativos" value={loading ? "..." : operationalMonitors.length} detail={`${summary?.monitor_agents ?? 0} con contrato`} />
            <CommandMetric icon={Cpu} label="Runs recientes" value={loading ? "..." : summary?.recent_runs ?? 0} detail={`${summary?.failed_recent_runs ?? 0} con error`} />
            <CommandMetric icon={AlertTriangle} label="Alertas agente" value={loading ? "..." : summary?.open_agent_alerts ?? 0} detail={`${summary?.agent_alerts_total ?? 0} históricas`} />
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <CommandMetric icon={BrainCircuit} label="Motores configurados" value={loading ? "..." : summary?.configured_engines ?? 0} detail={`${engines.filter((engine) => engine.status === "ready").length} con evidencia`} />
            <CommandMetric icon={Gauge} label="Monte Carlo" value={loading ? "..." : summary?.monte_carlo_simulations ?? 0} detail={engineDetail(monteCarloEngine, "simulaciones persistidas", "configurado sin simulaciones", "sin motor configurado")} />
            <CommandMetric icon={BrainCircuit} label="Bayes" value={loading ? "..." : summary?.bayesian_calibration_samples ?? 0} detail={engineDetail(bayesEngine, `${summary?.bayesian_calibration_states ?? 0} estados calibrados`, "configurado sin muestras", "sin calibración configurada")} />
            <CommandMetric icon={Cpu} label="Decision" value={loading ? "..." : summary?.decision_orchestrations ?? 0} detail="orquestaciones guardadas" />
          </div>
          {!loading && monteCarloEngine?.status === "configured" ? (
            <OperationalNotice tone="warning" title="Monte Carlo configurado sin evidencia">
              El motor existe, pero todavía no hay simulaciones persistidas para este workspace.
            </OperationalNotice>
          ) : null}
          {!loading && bayesEngine?.status === "configured" ? (
            <OperationalNotice tone="warning" title="Bayes configurado sin muestras suficientes">
              El motor existe, pero falta registrar outcomes/observaciones para calibrar probabilidades.
            </OperationalNotice>
          ) : null}
          {!loading && summary?.monitor_agents === 0 ? (
            <OperationalNotice tone="warning" title="Sin monitor operativo">
              No hay agentes de monitor workspace-scoped para ejecutar WisdomBits, Monte Carlo o Decision desde Control Room.
            </OperationalNotice>
          ) : null}
          {!loading && summary && summary.monitor_agents > 0 && operationalMonitors.length === 0 ? (
            <OperationalNotice tone="warning" title="Monitores sin tools operativas">
              Hay monitores registrados, pero no tienen tools AgentOps disponibles en el catálogo vivo.
            </OperationalNotice>
          ) : null}

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(300px,0.6fr)]">
            <div className="space-y-3">
              {agents.slice(0, 8).map((agent) => (
                <article key={agent.id} className="rounded-lg border bg-background p-3 dark:border-sky-400/15 dark:bg-[#06111f]">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="truncate text-sm font-semibold text-foreground dark:text-white">{agent.name}</h3>
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
                        {agent.cartridge_id} · {agent.role} · {cronLabel(agent.schedule)}
                      </p>
                    </div>
                    <div className="text-right text-xs text-muted-foreground">
                      <div>{agent.alerts.open} abiertas</div>
                      <div>{agent.alerts.total} alertas</div>
                    </div>
                  </div>
                  <div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
                    <span className="inline-flex items-center gap-1"><Clock3 className="h-3.5 w-3.5" /> {agent.last_run?.started_at ? new Date(agent.last_run.started_at).toLocaleString("es-MX") : "sin ejecución"}</span>
                    <span className="truncate">Dataset: {monitorDataset(agent)}</span>
                    <span className="inline-flex items-center gap-1 truncate"><Wrench className="h-3.5 w-3.5" /> {agent.operational_tools_count ?? agent.allowed_tools.length ?? 0} tools AgentOps</span>
                  </div>
                  {(agent.configured_engines ?? []).length ? (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {(agent.configured_engines ?? []).slice(0, 5).map((engine) => (
                        <span key={`${agent.id}:${engine.engine}:${engine.mode ?? "direct"}`} className={cn(
                          "rounded-full border px-2 py-0.5 text-[11px] font-medium",
                          engine.enabled
                            ? "border-cyan-400/30 bg-cyan-500/10 text-cyan-700 dark:text-cyan-200"
                            : "border-slate-300 bg-slate-100 text-slate-500 dark:border-sky-400/15 dark:bg-slate-900/50",
                        )}>
                          {engineLabel(engine.engine)}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </article>
              ))}
              {!loading && agents.length === 0 ? (
                <OperationalNotice tone="warning" title="Sin agentes visibles">No hay agentes workspace-scoped o globales para este contexto.</OperationalNotice>
              ) : null}
            </div>

            <div className="space-y-3">
              <div className="rounded-lg border bg-background p-3 dark:border-sky-400/15 dark:bg-[#06111f]">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-foreground dark:text-white">Motores</h3>
                  <span className="text-xs text-muted-foreground">{engines.length}</span>
                </div>
                <div className="space-y-2">
                  {engines.map((engine) => (
                    <div key={engine.engine} className="rounded-md border bg-muted/20 p-2 text-xs dark:border-sky-400/10 dark:bg-slate-950/30">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium text-foreground dark:text-white">{engineLabel(engine.engine)}</span>
                        <span className={cn("rounded-full border px-2 py-0.5 font-medium", engineStatusTone(engine.status))}>{engine.status}</span>
                      </div>
                      <div className="mt-1 flex items-center justify-between gap-2 text-muted-foreground">
                        <span>{engine.configured} configs</span>
                        <span>{engine.sample_count ?? engine.evidence_count} evidencia</span>
                      </div>
                    </div>
                  ))}
                  {!loading && engines.length === 0 ? (
                    <p className="text-xs text-muted-foreground">Sin motores registrados.</p>
                  ) : null}
                </div>
              </div>

              <div className="rounded-lg border bg-background p-3 dark:border-sky-400/15 dark:bg-[#06111f]">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-foreground dark:text-white">Origen de señales</h3>
                  <span className="text-xs text-muted-foreground">{payload?.origins.length ?? 0}</span>
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
                <h3 className="mb-2 text-sm font-semibold text-foreground dark:text-white">Tools usadas</h3>
                <div className="space-y-2">
                  {(payload?.tools_used ?? []).slice(0, 6).map((item) => (
                    <div key={item.tool} className="flex items-center justify-between gap-2 text-xs">
                      <span className="truncate text-muted-foreground">{item.tool}</span>
                      <span className="font-semibold text-foreground dark:text-white">{item.count}</span>
                    </div>
                  ))}
                  {!loading && (payload?.tools_used ?? []).length === 0 ? (
                    <p className="text-xs text-muted-foreground">Sin tool calls recientes.</p>
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
