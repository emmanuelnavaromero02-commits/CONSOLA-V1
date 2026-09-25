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
import type { AgentRunRecord } from "@/lib/admin-surfaces";
import type {
  ControlRoomAgentsOpsAgent,
  ControlRoomAgentsOpsEngine,
  ControlRoomAgentsOpsPayload,
} from "@/lib/control-room/types";
import { nextDailyRun, scheduleLabel, type WisdomBitMonitor } from "@/lib/control-room/wisdom-bit-monitors";
import { cn } from "@/lib/utils";

function labelForOrigin(origin?: string | null): string {
  const key = (origin || "").replaceAll("_", " ").trim();
  if (!key || key === "unknown") return "Sin origen";
  return key.replace(/\b\w/g, (char) => char.toUpperCase());
}

function statusTone(status?: string | null): string {
  if (status === "ok" || status === "success") return "border-emerald-400/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200";
  if (status === "error" || status === "failed" || status === "cancelled") return "border-rose-400/30 bg-rose-500/10 text-rose-700 dark:text-rose-200";
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

export interface AgentCardModel {
  key: string;
  name: string;
  status?: string | null;
  kind: "ready" | "incomplete" | "agent" | "inactive";
  kindLabel: string;
  role: string;
  alertsOpen?: number | null;
  alertsTotal?: number | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  toolsCount?: number | null;
  wisdomBitId?: string | null;
  stage?: string | null;
  schedule?: string | null;
  nextRunAt?: string | null;
  recentStatuses?: string[];
}

function kindTone(kind: AgentCardModel["kind"]): string {
  if (kind === "ready") return "border-emerald-400/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-200";
  if (kind === "incomplete") return "border-amber-400/30 bg-amber-500/10 text-amber-700 dark:text-amber-200";
  return "border-slate-300 bg-slate-100 text-slate-600 dark:border-sky-400/20 dark:bg-slate-900/60 dark:text-slate-300";
}

function runStatusLabel(status?: string | null): string {
  const labels: Record<string, string> = {
    ok: "ok",
    success: "ok",
    error: "error",
    failed: "error",
    cancelled: "cancelada",
    running: "en curso",
    queued: "en cola",
  };
  return status ? labels[status] || status : "sin run";
}

function localTime(value?: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleString("es-MX");
}

export function AgentOpsCard({ agent }: { agent: AgentCardModel }) {
  const started = localTime(agent.startedAt);
  const finished = localTime(agent.finishedAt);
  const nextRun = localTime(agent.nextRunAt);
  return (
    <article className="rounded-lg border bg-background p-3 dark:border-sky-400/15 dark:bg-[#06111f]">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {agent.wisdomBitId ? (
              <span className="rounded border bg-muted/40 px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">{agent.wisdomBitId}</span>
            ) : null}
            <h3 className="min-w-0 break-words text-sm font-semibold text-foreground dark:text-white">{agent.name}</h3>
            <span className={cn("rounded-full border px-2 py-0.5 text-xs font-medium", statusTone(agent.status))}>
              {runStatusLabel(agent.status)}
            </span>
            <span className={cn("rounded-full border px-2 py-0.5 text-xs font-medium", kindTone(agent.kind))}>
              {agent.kindLabel}
            </span>
            {agent.stage ? (
              <span className="rounded-full border border-cyan-400/30 bg-cyan-500/10 px-2 py-0.5 text-xs font-medium text-cyan-700 dark:text-cyan-200">{agent.stage}</span>
            ) : null}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">{agent.role}</p>
        </div>
        <div className="text-right text-xs text-muted-foreground">
          <div>{agent.alertsOpen == null ? "sin dato de alertas" : `${agent.alertsOpen} abiertas`}</div>
          {agent.alertsTotal == null ? null : <div>{agent.alertsTotal} alertas</div>}
        </div>
      </div>
      <div className="mt-3 grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
        <span className="inline-flex items-center gap-1"><Clock3 className="h-3.5 w-3.5" /> {started ?? "sin ejecución"}</span>
        <span className="truncate">{finished ? `Finalizó ${finished}` : "sin cierre registrado"}</span>
        {agent.schedule === undefined ? (
          <span className="inline-flex items-center gap-1 truncate"><Wrench className="h-3.5 w-3.5" /> {agent.toolsCount ?? 0} capacidades operativas</span>
        ) : null}
      </div>
      {agent.schedule !== undefined ? (
        <p className="mt-2 text-xs text-muted-foreground">
          {agent.schedule ? `Horario ${agent.schedule}` : "Sin horario"}
          {nextRun ? ` · próxima corrida ${nextRun}` : ""}
        </p>
      ) : null}
      {agent.recentStatuses?.length ? (
        <div className="mt-2 flex items-center gap-1 text-[11px] text-muted-foreground" aria-label="Corridas recientes">
          <span className="mr-1">Corridas recientes</span>
          {agent.recentStatuses.map((status, index) => (
            <span key={`${status}:${index}`} className={cn("rounded border px-1.5 py-0.5", statusTone(status))}>
              {runStatusLabel(status)}
            </span>
          ))}
        </div>
      ) : null}
    </article>
  );
}

function opsAgentCard(agent: ControlRoomAgentsOpsAgent, index: number): AgentCardModel {
  return {
    key: `${agent.name || "agent"}:${index}`,
    name: agent.name || "Agente",
    status: agent.last_run?.status,
    kind: agent.operationally_ready ? "ready" : agent.monitor ? "incomplete" : "agent",
    kindLabel: agent.operationally_ready ? "monitor operativo" : agent.monitor ? "monitor incompleto" : "agente",
    role: agent.role || "Rol operativo",
    alertsOpen: agent.alerts.open,
    alertsTotal: agent.alerts.total,
    startedAt: agent.last_run?.started_at,
    finishedAt: agent.last_run?.finished_at,
    toolsCount: agent.operational_tools_count,
  };
}

export interface WisdomBitScope {
  prefix: string;
  monitors: WisdomBitMonitor[];
  runs: Record<string, { runs: AgentRunRecord[]; loading: boolean; failed: boolean }>;
  expected?: string[];
  stages?: Record<string, string>;
  loading?: boolean;
  error?: string;
  now?: Date;
}

export function wisdomBitCards(scope: WisdomBitScope, payload: ControlRoomAgentsOpsPayload | null): AgentCardModel[] {
  const opsByName = new Map((payload?.agents ?? []).map((agent) => [agent.name ?? "", agent]));
  return scope.monitors.map((monitor) => {
    const runs = scope.runs[monitor.id]?.runs ?? [];
    const last = runs[0];
    const ops = opsByName.get(monitor.name);
    const schedule = scheduleLabel(monitor.cron);
    return {
      key: monitor.id,
      name: monitor.name,
      status: last?.status,
      kind: monitor.active ? "ready" : "inactive",
      kindLabel: monitor.active ? "activo" : "inactivo",
      role: monitor.description || "Monitor WisdomBit",
      alertsOpen: ops ? ops.alerts.open : null,
      alertsTotal: ops ? ops.alerts.total : null,
      startedAt: last?.started_at,
      finishedAt: last?.finished_at,
      wisdomBitId: monitor.wisdomBitId,
      stage: scope.stages?.[monitor.wisdomBitId] ?? null,
      schedule: schedule ? `${schedule}${monitor.timeZone ? ` ${monitor.timeZone}` : ""}` : null,
      nextRunAt: monitor.active ? nextDailyRun(monitor.cron, monitor.timeZone, scope.now)?.toISOString() ?? null : null,
      recentStatuses: runs.map((run) => run.status || "sin estado"),
    };
  });
}

function ScopedAgentsBody({ scope, payload }: { scope: WisdomBitScope; payload: ControlRoomAgentsOpsPayload | null }) {
  const cards = wisdomBitCards(scope, payload);
  const registered = new Set(scope.monitors.map((monitor) => monitor.wisdomBitId));
  const missing = (scope.expected ?? []).filter((id) => !registered.has(id));
  const active = scope.monitors.filter((monitor) => monitor.active).length;
  const failedRuns = Object.values(scope.runs).reduce(
    (total, entry) => total + entry.runs.filter((run) => run.status === "error" || run.status === "failed").length,
    0,
  );
  const openAlerts = cards.reduce((total, card) => total + (card.alertsOpen ?? 0), 0);
  const expectedTotal = scope.expected?.length ?? scope.monitors.length;
  return (
    <div className="space-y-4 border-t px-4 py-4 dark:border-sky-400/15">
      {scope.error ? <OperationalNotice tone="error" title="Monitores no disponibles">{scope.error}</OperationalNotice> : null}
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <CommandMetric icon={ShieldCheck} label="Monitores registrados" value={scope.loading ? "..." : `${scope.monitors.length}/${expectedTotal}`} detail={`${scope.prefix}*`} />
        <CommandMetric icon={Bot} label="Activos" value={scope.loading ? "..." : active} detail={`${scope.monitors.length - active} inactivos`} />
        <CommandMetric icon={Cpu} label="Corridas con error" value={scope.loading ? "..." : failedRuns} detail="últimas corridas por monitor" tone={failedRuns ? "warning" : "neutral"} />
        <CommandMetric icon={AlertTriangle} label="Alertas abiertas" value={scope.loading ? "..." : payload ? openAlerts : "N/D"} detail={payload ? "de estos monitores" : "sin dato de alertas"} tone={openAlerts ? "warning" : "neutral"} />
      </div>
      {!scope.loading && missing.length ? (
        <OperationalNotice tone="warning" title="Monitores sin registrar en este workspace">
          {missing.join(", ")}
        </OperationalNotice>
      ) : null}
      <div className="space-y-3">
        {cards.map((card) => <AgentOpsCard key={card.key} agent={card} />)}
        {!scope.loading && !scope.error && cards.length === 0 ? (
          <OperationalNotice tone="warning" title="Sin monitores visibles">
            No hay agentes {scope.prefix}* registrados para este workspace.
          </OperationalNotice>
        ) : null}
      </div>
    </div>
  );
}

export function AgentsOpsPanel({
  payload,
  loading,
  error,
  collapsed,
  onToggle,
  scope,
}: {
  payload: ControlRoomAgentsOpsPayload | null;
  loading: boolean;
  error: string;
  collapsed: boolean;
  onToggle: () => void;
  scope?: WisdomBitScope;
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
          <span className="mt-1 block text-lg font-semibold text-foreground dark:text-white">
            {scope ? `Monitores ${scope.prefix}*` : "Agentes, WisdomBits y análisis"}
          </span>
        </span>
        <span className="flex items-center gap-2 text-sm text-muted-foreground">
          {scope
            ? scope.loading ? "cargando" : `${scope.monitors.length} registrados`
            : summary ? `${operationalMonitors.length}/${summary.monitor_agents} monitores operativos · ${summary.open_agent_alerts} alertas` : loading ? "cargando" : "sin datos"}
          {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </span>
      </button>

      {collapsed ? null : scope ? (
        <ScopedAgentsBody scope={scope} payload={payload} />
      ) : (
        <div className="space-y-4 border-t px-4 py-4 dark:border-sky-400/15">
          {error ? <OperationalNotice tone="error" title="Agentes no disponibles">No se pudo actualizar esta vista.</OperationalNotice> : null}
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <CommandMetric icon={Bot} label="Agentes activos" value={loading ? "..." : summary?.active_agents ?? 0} detail={`${summary?.agents_total ?? 0} registrados`} />
            <CommandMetric icon={ShieldCheck} label="Monitores operativos" value={loading ? "..." : operationalMonitors.length} detail={`${summary?.monitor_agents ?? 0} con contrato`} />
            <CommandMetric icon={Cpu} label="Runs recientes" value={loading ? "..." : summary?.recent_runs ?? 0} detail={`${summary?.failed_recent_runs ?? 0} con error`} />
            <CommandMetric icon={AlertTriangle} label="Alertas agente" value={loading ? "..." : summary?.open_agent_alerts ?? 0} detail={`${summary?.agent_alerts_total ?? 0} históricas`} />
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <CommandMetric icon={BrainCircuit} label="Capacidades configuradas" value={loading ? "..." : summary?.configured_engines ?? 0} detail={`${engines.filter((engine) => engine.status === "ready").length} con evidencia`} />
            <CommandMetric icon={Gauge} label="Análisis operativo" value={loading ? "..." : summary?.monte_carlo_simulations ?? 0} detail={engineDetail(monteCarloEngine, "resultados persistidos", "en espera de datos", "sin análisis configurado")} />
            <CommandMetric icon={BrainCircuit} label="Historial operativo" value={loading ? "..." : summary?.bayesian_calibration_samples ?? 0} detail={engineDetail(bayesEngine, `${summary?.bayesian_calibration_states ?? 0} estados con historial`, "requiere historial adicional", "sin historial configurado")} />
            <CommandMetric icon={Cpu} label="Decisión" value={loading ? "..." : summary?.decision_orchestrations ?? 0} detail="orquestaciones guardadas" />
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
                <AgentOpsCard key={`${agent.name || "agent"}:${index}`} agent={opsAgentCard(agent, index)} />
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
