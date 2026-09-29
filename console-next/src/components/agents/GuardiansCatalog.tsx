"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, Play, RefreshCw, Settings2, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import {
  getMeAccess,
  invokeAgent,
  listAgentRuns,
  listAgents,
  type AgentRecord,
  type AgentRunRecord,
} from "@/lib/admin-surfaces";
import { isApiError } from "@/lib/api";
import { dataSourceName } from "@/lib/glossary";
import { describeSchedule } from "@/lib/schedule/frequency";
import { runStatusCopy } from "@/lib/status-copy";
import { cn } from "@/lib/utils";

const RECENT_RUNS = 5;

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function isGuardian(agent: AgentRecord): boolean {
  const extra = asRecord(agent.extra);
  return asText(extra.role) === "monitor" || Object.keys(asRecord(extra.monitor)).length > 0;
}

export function guardianSchedule(agent: AgentRecord): { cron: string; tz: string; enabled: boolean; prompt: string } {
  const extra = asRecord(agent.extra);
  const schedule = asRecord(extra.schedule ?? asRecord(extra.monitor).schedule);
  return {
    cron: asText(schedule.cron || schedule.cron_expression).trim(),
    tz: asText(schedule.tz).trim(),
    enabled: schedule.enabled !== false,
    prompt: asText(schedule.prompt).trim(),
  };
}

export function inspectionMessage(agent: AgentRecord): string {
  const prompt = guardianSchedule(agent).prompt;
  if (prompt) return prompt;
  return "Ejecuta una revision operativa ahora usando tu contrato de monitor. Registra evidencia agregada, respeta recommendation_only y no hagas write-back externo.";
}

type HealthTone = "ok" | "bad" | "busy" | "none";

export function guardianHealth(run: AgentRunRecord | undefined): { label: string; tone: HealthTone } {
  if (!run) return { label: "Sin corridas registradas", tone: "none" };
  const status = String(run.status ?? "").trim().toLowerCase();
  if (["ok", "success", "succeeded", "done", "completed"].includes(status)) return { label: "Última corrida exitosa", tone: "ok" };
  if (["error", "failed", "failure"].includes(status)) return { label: "La última corrida falló", tone: "bad" };
  if (["running", "in_progress"].includes(status)) return { label: "Corrida en curso", tone: "busy" };
  if (["queued", "pending"].includes(status)) return { label: "Corrida en cola", tone: "busy" };
  return { label: "Sin información", tone: "none" };
}

function healthClass(tone: HealthTone): string {
  if (tone === "ok") return "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
  if (tone === "bad") return "border-destructive/40 bg-destructive/10 text-destructive";
  if (tone === "busy") return "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300";
  return "border-border bg-muted/40 text-muted-foreground";
}

function formatRunDate(value: string | null | undefined): string {
  if (!value) return "";
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return "";
  return new Intl.DateTimeFormat("es", { dateStyle: "short", timeStyle: "short" }).format(new Date(parsed));
}

export function GuardiansCatalog({ onOpenAdmin }: { onOpenAdmin: () => void }) {
  const queryClient = useQueryClient();

  const agents = useQuery({ queryKey: ["agents"], queryFn: listAgents, staleTime: 30_000 });
  const access = useQuery({ queryKey: ["me", "access"], queryFn: getMeAccess, staleTime: 60_000 });
  const canExecute = (access.data?.permissions ?? []).includes("agents.execute");

  const guardians = (agents.data ?? []).filter(isGuardian);

  const runQueries = useQueries({
    queries: guardians.map((guardian) => ({
      queryKey: ["agents", guardian.id, "runs", RECENT_RUNS],
      queryFn: () => listAgentRuns(guardian.id, RECENT_RUNS),
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    })),
  });

  const inspect = useMutation({
    mutationFn: async (guardian: AgentRecord) => {
      await invokeAgent(guardian.id, inspectionMessage(guardian), [], { background: true });
      return guardian;
    },
    onSuccess: (guardian) => {
      toast.success(`Inspección iniciada: ${guardian.name}.`);
      queryClient.invalidateQueries({ queryKey: ["agents", guardian.id, "runs", RECENT_RUNS] });
      if (typeof window !== "undefined") {
        window.setTimeout(() => {
          queryClient.invalidateQueries({ queryKey: ["agents", guardian.id, "runs", RECENT_RUNS] });
        }, 4_000);
      }
    },
    onError: (error) => {
      const requestId = isApiError(error) ? error.requestId : undefined;
      toast.error(requestId ? `No se pudo iniciar la inspección. Ref: ${requestId}` : "No se pudo iniciar la inspección.");
    },
  });

  return (
    <div className="space-y-4">
      <header className="flex flex-col gap-3 rounded-lg border bg-card p-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <ShieldCheck aria-hidden className="h-5 w-5 text-primary" />
            <h2 className="text-lg font-semibold">Guardianes del negocio</h2>
          </div>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Agentes que vigilan indicadores del negocio con la frecuencia acordada, solo recomiendan y dejan evidencia de cada corrida.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => agents.refetch()}
            className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md border bg-background"
            aria-label="Refrescar guardianes"
          >
            <RefreshCw aria-hidden className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={onOpenAdmin}
            className="inline-flex min-h-[44px] items-center gap-2 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5"
          >
            <Settings2 aria-hidden className="h-4 w-4" />
            Administración técnica
          </button>
        </div>
      </header>

      {agents.isLoading ? (
        <div aria-busy="true" className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {Array.from({ length: 4 }).map((_, index) => (
            <span key={index} className="block h-52 animate-pulse rounded-lg bg-muted" aria-hidden />
          ))}
        </div>
      ) : agents.isError ? (
        <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
          <p className="font-medium text-destructive">No se pudieron cargar los guardianes.</p>
          <button
            type="button"
            onClick={() => agents.refetch()}
            className="mt-2 inline-flex min-h-[44px] items-center gap-2 rounded-md border px-3 text-xs font-medium"
          >
            <RefreshCw aria-hidden className="h-4 w-4" />
            Reintentar
          </button>
        </div>
      ) : guardians.length === 0 ? (
        <div className="rounded-lg border bg-muted/20 p-6 text-sm">
          <h3 className="font-semibold">No hay guardianes registrados en este espacio de trabajo</h3>
          <p className="mt-1 max-w-3xl text-muted-foreground">
            Los guardianes se aprovisionan por espacio de trabajo desde la plataforma. Cuando existan, aquí verás su objetivo,
            frecuencia y últimas corridas. Los demás agentes siguen disponibles en la administración técnica.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {guardians.map((guardian, index) => {
            const runs = runQueries[index]?.data ?? [];
            const runsFailed = Boolean(runQueries[index]?.isError);
            const lastRun = runs[0];
            const health = guardianHealth(lastRun);
            const schedule = guardianSchedule(guardian);
            const active = guardian.is_active !== false;
            const lastRunDate = formatRunDate(lastRun?.started_at || lastRun?.finished_at);
            return (
              <article key={guardian.id || index} data-testid="guardian-card" className="flex flex-col gap-3 rounded-lg border bg-card p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="text-base font-semibold" title={guardian.id}>{guardian.name}</h3>
                    <p className="text-xs text-muted-foreground">{dataSourceName(guardian.cartridge_id)}</p>
                  </div>
                  <span className={cn("rounded-full border px-2.5 py-1 text-xs font-medium", healthClass(active ? health.tone : "none"))}>
                    {active ? health.label : "Inactivo"}
                  </span>
                </div>
                <p className="text-sm text-muted-foreground">
                  {guardian.description?.trim() || "Sin información"}
                </p>
                <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
                  <div className="rounded-md border bg-background p-2.5">
                    <dt className="flex items-center gap-1.5 text-xs uppercase tracking-wide text-muted-foreground">
                      <Clock aria-hidden className="h-3.5 w-3.5" />
                      Frecuencia
                    </dt>
                    <dd className="mt-1 font-medium">
                      {schedule.cron && !schedule.enabled ? "Tarea pausada" : describeSchedule(schedule.cron, schedule.tz)}
                    </dd>
                  </div>
                  <div className="rounded-md border bg-background p-2.5">
                    <dt className="text-xs uppercase tracking-wide text-muted-foreground">Última corrida</dt>
                    <dd className="mt-1 font-medium">
                      {runsFailed
                        ? "Sin información"
                        : lastRun
                          ? `${runStatusCopy(lastRun.status)}${lastRunDate ? ` · ${lastRunDate}` : ""}`
                          : "Sin corridas registradas"}
                    </dd>
                  </div>
                </dl>
                {canExecute && active ? (
                  <div>
                    <button
                      type="button"
                      disabled={inspect.isPending}
                      onClick={() => inspect.mutate(guardian)}
                      className="inline-flex min-h-[44px] items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-60"
                    >
                      <Play aria-hidden className="h-4 w-4" />
                      {inspect.isPending ? "Iniciando..." : "Inspeccionar ahora"}
                    </button>
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
