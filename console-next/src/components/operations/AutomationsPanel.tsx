"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { CalendarClock, Loader2, RefreshCw, Workflow } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { listAutomations, type Automation } from "@/lib/operations/automations-client";
import { cn } from "@/lib/utils";

export const AUTOMATIONS_TITLE = "Automatizaciones en segundo plano";
export const AUTOMATIONS_ERROR = "No se pudieron cargar las automatizaciones.";
export const AIRFLOW_UNAVAILABLE_NOTE =
  "No se pudo consultar Airflow; el estado de cada automatización se muestra como desconocido.";

const STATE_LABELS: Record<Automation["state"], string> = {
  active: "Activa",
  paused_by_operator: "En pausa por operador",
  paused_manual: "En pausa",
  unavailable: "No disponible",
};

const STATE_TONES: Record<Automation["state"], string> = {
  active: "border-success/30 bg-success/10 text-foreground",
  paused_by_operator: "border-warning/40 bg-warning/10 text-foreground",
  paused_manual: "bg-muted text-muted-foreground",
  unavailable: "border-destructive/30 bg-destructive/5 text-destructive",
};

const RUN_LABELS: Record<string, string> = {
  success: "Exitosa",
  failed: "Fallida",
  running: "En curso",
  queued: "En cola",
  up_for_retry: "Reintento pendiente",
  skipped: "Omitida",
};

export const automationsQueryKey = ["operations", "automations"] as const;

function formatDate(value: string | null): string | null {
  if (!value) return null;
  return new Intl.DateTimeFormat("es-MX", { dateStyle: "short", timeStyle: "short" }).format(
    new Date(value),
  );
}

function activeRuns(automation: Automation): string {
  if (!automation.runs_known || automation.active_runs === null) return "Sin información";
  return String(automation.active_runs);
}

function lastRun(automation: Automation): string {
  const run = automation.last_run;
  if (!automation.runs_known) return "Sin información";
  if (run === null) return "Sin ejecuciones registradas";
  const label = RUN_LABELS[run.status] ?? run.status;
  const when = formatDate(run.finished_at ?? run.started_at);
  return when ? `${label} · ${when}` : label;
}

export function AutomationsPanel() {
  const query = useQuery({
    queryKey: automationsQueryKey,
    queryFn: listAutomations,
    retry: false,
    staleTime: 30_000,
    refetchOnWindowFocus: true,
  });
  const automations = query.data?.automations ?? [];

  return (
    <section className="rounded-lg border bg-card shadow-sm" aria-labelledby="automations-title">
      <header className="flex flex-col gap-2 border-b px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 id="automations-title" className="text-base font-semibold">
            {AUTOMATIONS_TITLE}
          </h2>
          <p className="text-xs text-muted-foreground">
            Estado leído de Airflow. Esta vista es de solo lectura.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void query.refetch()}
            aria-label="Actualizar automatizaciones"
            className="inline-flex min-h-[40px] min-w-[40px] items-center justify-center rounded-md border bg-background hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <RefreshCw aria-hidden className={cn("h-4 w-4", query.isFetching && "animate-spin")} />
          </button>
          <Link
            href="/monitor"
            className="inline-flex min-h-[40px] items-center justify-center rounded-md border bg-background px-3 text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Ver ejecuciones
          </Link>
        </div>
      </header>

      {query.isPending ? (
        <div role="status" aria-busy="true" className="flex items-center gap-2 px-4 py-6 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
          Cargando automatizaciones…
        </div>
      ) : query.isError ? (
        <div role="alert" className="m-4 flex flex-col gap-3 rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive sm:flex-row sm:items-center sm:justify-between">
          <span>{AUTOMATIONS_ERROR}</span>
          <button
            type="button"
            onClick={() => void query.refetch()}
            className="inline-flex min-h-[40px] items-center justify-center rounded-md border bg-background px-3 text-sm font-medium text-foreground hover:bg-muted"
          >
            Reintentar
          </button>
        </div>
      ) : automations.length === 0 ? (
        <EmptyState
          icon={Workflow}
          size="sm"
          title="Sin automatizaciones visibles"
          description="No hay procesos registrados para las fuentes de datos de este espacio de trabajo."
        />
      ) : (
        <>
          {query.data && !query.data.airflow_available ? (
            <p className="border-b px-4 py-2 text-sm text-muted-foreground">{AIRFLOW_UNAVAILABLE_NOTE}</p>
          ) : null}
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y text-sm">
              <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="px-4 py-3 text-left font-medium">Automatización</th>
                  <th className="px-4 py-3 text-left font-medium">Fuente de datos</th>
                  <th className="px-4 py-3 text-left font-medium">Tipo</th>
                  <th className="px-4 py-3 text-left font-medium">Estado</th>
                  <th className="px-4 py-3 text-left font-medium">Ejecuciones activas</th>
                  <th className="px-4 py-3 text-left font-medium">Última ejecución</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {automations.map((automation) => (
                  <tr key={automation.dag_id} className="align-top" data-automation={automation.dag_id}>
                    <td className="max-w-sm px-4 py-3">
                      <div className="font-medium text-foreground">{automation.label}</div>
                      <div className="mt-1 truncate font-mono text-xs text-muted-foreground">
                        {automation.dag_id}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {automation.cartridge_id ?? "Plataforma"}
                    </td>
                    <td className="px-4 py-3">
                      <div>{automation.kind === "scheduled" ? "Programada" : "Manual"}</div>
                      {automation.schedule_description ? (
                        <div className="mt-1 inline-flex items-center gap-1 text-xs text-muted-foreground">
                          <CalendarClock aria-hidden className="h-3.5 w-3.5" />
                          {automation.schedule_description}
                        </div>
                      ) : null}
                    </td>
                    <td className="max-w-xs px-4 py-3">
                      <span className={cn("inline-flex rounded-md border px-2 py-0.5 text-xs font-medium", STATE_TONES[automation.state])}>
                        {STATE_LABELS[automation.state]}
                      </span>
                      <p className="mt-1 text-xs text-muted-foreground">{automation.state_note_es}</p>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">{activeRuns(automation)}</td>
                    <td className="px-4 py-3 text-muted-foreground">{lastRun(automation)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
