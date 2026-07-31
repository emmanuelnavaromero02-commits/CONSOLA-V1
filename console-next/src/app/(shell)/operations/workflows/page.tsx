"use client";

import { useMemo, useState, type ReactNode } from "react";
import { Ban, ClipboardList, GitBranch, Info, Loader2, RefreshCw, Search, Workflow } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { toast } from "sonner";

import {
  useCancelOperationWorkflow,
  useOperationWorkflow,
  useOperationWorkflows,
  usePlanOperationWorkflow,
} from "@/lib/operations/hooks";
import type { OperationWorkflow, OperationWorkflowStep } from "@/lib/operations/types";
import { cn } from "@/lib/utils";

const ACTIVE_STATUSES = new Set(["planning", "running", "waiting_approval"]);
const TERMINAL_STATUSES = new Set(["completed", "cancelled", "failed"]);

export default function OperationsWorkflowsPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const workflows = useOperationWorkflows();
  const selected = useOperationWorkflow(selectedId);
  const plan = usePlanOperationWorkflow();
  const cancel = useCancelOperationWorkflow();

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const list = workflows.data ?? [];
    if (!needle) return list;
    return list.filter((workflow) => (
      workflow.id.toLowerCase().includes(needle)
      || (workflow.intent ?? "").toLowerCase().includes(needle)
      || workflow.status.toLowerCase().includes(needle)
    ));
  }, [search, workflows.data]);

  const metrics = useMemo(() => {
    const list = workflows.data ?? [];
    return {
      total: list.length,
      active: list.filter((workflow) => ACTIVE_STATUSES.has(workflow.status)).length,
      failed: list.filter((workflow) => workflow.status === "failed").length,
    };
  }, [workflows.data]);
  const metricsUnavailable = workflows.isLoading || (workflows.isError && !workflows.data);
  const actionPending = plan.isPending || cancel.isPending;

  async function planWorkflow(workflow: OperationWorkflow) {
    try {
      await plan.mutateAsync(workflow);
      toast.success("Planificación solicitada.");
      setSelectedId(workflow.id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "No se pudo planificar el workflow.");
    }
  }

  async function cancelWorkflow(workflow: OperationWorkflow) {
    try {
      await cancel.mutateAsync(workflow.id);
      toast.success("Workflow cancelado.");
      setSelectedId(workflow.id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "No se pudo cancelar el workflow.");
    }
  }

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-6">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Workflows</h1>
          <p className="text-sm text-muted-foreground">
            Flujos operativos del Copiloto con planificación y cancelación controlada.
          </p>
        </div>
        <button
          type="button"
          onClick={() => workflows.refetch()}
          className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RefreshCw aria-hidden className={cn("h-4 w-4", workflows.isFetching && "animate-spin")} />
          Refrescar
        </button>
      </header>

      <p role="note" className="flex items-start gap-2 rounded-md border border-primary/30 bg-primary/5 p-3 text-sm text-muted-foreground">
        <Info aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        La ejecución de workflows no está disponible desde esta consola: solo se permite planificar y revisar en modo preview.
      </p>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-3" aria-label="Resumen de workflows">
        <MetricCard icon={Workflow} label="Total" value={metrics.total} loading={metricsUnavailable} />
        <MetricCard icon={GitBranch} label="Activos" value={metrics.active} loading={metricsUnavailable} />
        <MetricCard icon={Ban} label="Fallidos" value={metrics.failed} loading={metricsUnavailable} />
      </section>

      <section className="rounded-lg border bg-card p-4 shadow-sm">
        <label className="block space-y-1 text-sm">
          <span className="text-xs font-medium uppercase text-muted-foreground">Buscar</span>
          <div className="relative">
            <Search aria-hidden className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="min-h-[44px] w-full rounded-md border bg-background pl-9 pr-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              placeholder="Intent, estado o ID"
            />
          </div>
        </label>
      </section>

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(380px,0.9fr)]">
        <div className="rounded-lg border bg-card shadow-sm">
          <header className="border-b px-4 py-3">
            <h2 className="text-base font-semibold">Flujos actuales</h2>
          </header>
          {workflows.isError ? (
            <ErrorPanel message="No se pudieron cargar workflows." onRetry={() => workflows.refetch()} />
          ) : workflows.isLoading ? (
            <SkeletonRows rows={6} />
          ) : (
            <WorkflowsTable
              rows={rows}
              selectedId={selectedId}
              planPendingId={plan.variables?.id}
              cancelPendingId={cancel.variables}
              actionPending={actionPending}
              onSelect={setSelectedId}
              onPlan={planWorkflow}
              onCancel={cancelWorkflow}
            />
          )}
        </div>

        <WorkflowDetail
          selectedId={selectedId}
          loading={selected.isLoading}
          error={selected.isError}
          workflow={selected.data?.workflow ?? null}
          steps={selected.data?.steps ?? []}
          onRetry={() => selected.refetch()}
        />
      </section>
    </main>
  );
}

function WorkflowsTable({
  rows,
  selectedId,
  planPendingId,
  cancelPendingId,
  actionPending,
  onSelect,
  onPlan,
  onCancel,
}: {
  rows: OperationWorkflow[];
  selectedId: string | null;
  planPendingId?: string;
  cancelPendingId?: string;
  actionPending: boolean;
  onSelect: (id: string) => void;
  onPlan: (workflow: OperationWorkflow) => void;
  onCancel: (workflow: OperationWorkflow) => void;
}) {
  if (rows.length === 0) return <EmptyState label="Sin workflows visibles." />;

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y text-sm">
        <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-4 py-3 text-left font-medium">Intent</th>
            <th className="px-4 py-3 text-left font-medium">Estado</th>
            <th className="px-4 py-3 text-left font-medium">Creado</th>
            <th className="px-4 py-3 text-right font-medium">Acciones</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((workflow) => {
            const active = selectedId === workflow.id;
            const canCancel = ACTIVE_STATUSES.has(workflow.status);
            // Solo se planifica un workflow que no está en un estado terminal
            // ni ya activo (planning/running/waiting_approval).
            const canPlan = !TERMINAL_STATUSES.has(workflow.status) && !ACTIVE_STATUSES.has(workflow.status);
            return (
              <tr key={workflow.id} className={cn("align-top", active && "bg-primary/5")}>
                <td className="max-w-md px-4 py-3">
                  <button
                    type="button"
                    onClick={() => onSelect(workflow.id)}
                    className="block text-left font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {workflow.intent || workflow.id}
                  </button>
                  <div className="mt-1 truncate font-mono text-xs text-muted-foreground">{workflow.id}</div>
                  {workflow.error ? <div className="mt-1 text-xs text-destructive">{workflow.error}</div> : null}
                </td>
                <td className="px-4 py-3"><StatusBadge status={workflow.status} /></td>
                <td className="px-4 py-3 text-muted-foreground">{formatDate(workflow.created_at)}</td>
                <td className="px-4 py-3">
                  <div className="flex justify-end gap-2">
                    <button
                      type="button"
                      onClick={() => onPlan(workflow)}
                      disabled={!canPlan || actionPending}
                      className="inline-flex min-h-[40px] items-center justify-center gap-1.5 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      {planPendingId === workflow.id ? <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" /> : <ClipboardList aria-hidden className="h-3.5 w-3.5" />}
                      Planificar
                    </button>
                    <button
                      type="button"
                      onClick={() => onCancel(workflow)}
                      disabled={!canCancel || actionPending}
                      className="inline-flex min-h-[40px] items-center justify-center gap-1.5 rounded-md border border-destructive/40 bg-background px-3 text-xs font-medium text-destructive hover:bg-destructive/5 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      {cancelPendingId === workflow.id ? <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" /> : <Ban aria-hidden className="h-3.5 w-3.5" />}
                      Cancelar
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function WorkflowDetail({
  selectedId,
  loading,
  error,
  workflow,
  steps,
  onRetry,
}: {
  selectedId: string | null;
  loading: boolean;
  error: boolean;
  workflow: OperationWorkflow | null;
  steps: OperationWorkflowStep[];
  onRetry: () => void;
}) {
  return (
    <aside className="rounded-lg border bg-card shadow-sm">
      <header className="border-b px-4 py-3">
        <h2 className="text-base font-semibold">Detalle</h2>
      </header>
      {!selectedId ? (
        <EmptyState label="Selecciona un workflow para ver sus pasos." />
      ) : error ? (
        <ErrorPanel message="No se pudo cargar el detalle." onRetry={onRetry} />
      ) : loading ? (
        <SkeletonRows rows={4} />
      ) : workflow ? (
        <div className="space-y-4 p-4">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={workflow.status} />
              {workflow.current_step !== null && workflow.current_step !== undefined ? (
                <span className="rounded-md border bg-background px-2 py-0.5 text-xs text-muted-foreground">
                  Paso {workflow.current_step}
                </span>
              ) : null}
            </div>
            <h3 className="text-sm font-semibold">{workflow.intent || workflow.id}</h3>
            <p className="font-mono text-xs text-muted-foreground">{workflow.id}</p>
          </div>
          {workflow.error ? <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{workflow.error}</div> : null}
          <div className="space-y-2">
            {steps.length === 0 ? (
              <EmptyState label="Sin pasos materializados." />
            ) : steps.map((step) => (
              <article key={`${step.workflow_id}:${step.step_idx}`} className="rounded-md border bg-background p-3 text-sm">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h4 className="font-medium">Paso {step.step_idx + 1}</h4>
                    <p className="mt-1 text-muted-foreground">{step.description || "Sin descripción"}</p>
                  </div>
                  <StatusBadge status={step.status} />
                </div>
                {step.tool ? <p className="mt-2 truncate font-mono text-xs text-muted-foreground">{step.tool}</p> : null}
                {step.result ? (
                  <pre className="mt-2 max-h-32 overflow-auto rounded-md bg-muted p-2 text-xs text-muted-foreground">
                    {stringify(step.result)}
                  </pre>
                ) : null}
              </article>
            ))}
          </div>
        </div>
      ) : (
        <EmptyState label="Sin detalle disponible." />
      )}
    </aside>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  loading,
}: {
  icon: LucideIcon;
  label: string;
  value: ReactNode;
  loading?: boolean;
}) {
  return (
    <div className="rounded-lg border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold" aria-busy={loading ? true : undefined}>
            {loading ? (
              <>
                <span aria-hidden>...</span>
                <span className="sr-only">Cargando</span>
              </>
            ) : value}
          </p>
        </div>
        <span className="inline-flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Icon aria-hidden className="h-5 w-5" />
        </span>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const tone = statusTone(status);
  return (
    <span className={cn("inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium", tone)}>
      {status}
    </span>
  );
}

function statusTone(status: string): string {
  if (status === "completed") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
  if (status === "running") return "border-primary/30 bg-primary/10 text-primary";
  if (status === "waiting_approval" || status === "planning") return "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300";
  if (status === "failed") return "border-destructive/30 bg-destructive/10 text-destructive";
  return "bg-muted/40 text-muted-foreground";
}

function ErrorPanel({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="m-4 rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <span>{message}</span>
        <button type="button" onClick={onRetry} className="inline-flex min-h-[40px] items-center justify-center rounded-md border bg-background px-3 text-sm font-medium text-foreground hover:bg-accent/5">
          Reintentar
        </button>
      </div>
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return <div className="px-4 py-10 text-center text-sm text-muted-foreground">{label}</div>;
}

function SkeletonRows({ rows }: { rows: number }) {
  return (
    <div role="status" className="divide-y">
      <span className="sr-only">Cargando</span>
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="grid grid-cols-4 gap-4 px-4 py-4">
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "n/a";
  return new Intl.DateTimeFormat("es-ES", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
}

function stringify(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
