"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Ban,
  ClipboardCheck,
  Info,
  Loader2,
  RefreshCw,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import {
  getSupervisedAction,
  listSupervisedActions,
} from "@/lib/supervised-actions/client";
import type { SupervisedAction } from "@/lib/supervised-actions/types";
import { cn } from "@/lib/utils";

import { useSupervisedActionMutations } from "./use-action-mutations";

const EMPTY_ACTIONS: SupervisedAction[] = [];

// Estados terminales según el contrato (status/state ya entregados por la
// API): sobre una acción ejecutada o cancelada no procede ninguna mutación.
const TERMINAL_STATES = new Set(["executed", "completed", "cancelled", "canceled"]);

function actionId(action: SupervisedAction): string {
  return String(action.id || action.action_id || "");
}

function actionState(action: SupervisedAction): string {
  return String(action.status || action.state || "");
}

function isTerminal(action: SupervisedAction): boolean {
  return TERMINAL_STATES.has(actionState(action));
}

function actionTitle(action: SupervisedAction): string {
  return String(action.title || action.label || action.action_type || "Acción supervisada");
}

function stateLabel(status?: string): string {
  switch (status) {
    case "prepared":
    case "proposed":
    case "pending":
      return "Preparada";
    case "validated":
    case "dry_run_passed":
      return "Validada";
    case "requires_approval":
    case "awaiting_approval":
      return "Requiere aprobación";
    case "approved":
      return "Aprobada";
    case "executed":
    case "completed":
      return "Ejecutada";
    case "cancelled":
    case "canceled":
      return "Cancelada";
    case "validation_failed":
    case "dry_run_failed":
      return "Falló validación";
    case "failed":
      return "Requiere revisión";
    default:
      return status || "Sin estado";
  }
}

function shortDate(value?: string | null): string {
  if (!value) return "Sin fecha";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Sin fecha";
  return parsed.toLocaleString("es", { dateStyle: "short", timeStyle: "short" });
}

export default function SupervisedActionsPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const actions = useQuery({
    queryKey: ["supervised-actions"],
    queryFn: () => listSupervisedActions(100),
    staleTime: 20_000,
  });
  const selected = useQuery({
    queryKey: ["supervised-actions", selectedId],
    queryFn: () => getSupervisedAction(selectedId || ""),
    enabled: Boolean(selectedId),
    staleTime: 10_000,
  });

  const rows = actions.data?.actions ?? EMPTY_ACTIONS;
  const activeAction = selected.data || rows.find((row) => actionId(row) === selectedId) || rows[0];
  const currentId = activeAction ? actionId(activeAction) : "";
  const terminal = activeAction ? isTerminal(activeAction) : false;

  const refresh = async () => {
    await Promise.all([
      actions.refetch(),
      selectedId ? selected.refetch() : Promise.resolve(),
    ]);
  };

  // Idempotencia por intención lógica: la clave se conserva entre
  // reintentos y solo rota tras éxito definitivo (ver use-action-mutations).
  // La aprobación legacy no tiene camino interactivo: PR-A sigue sin
  // capacidad server-authoritative que la habilite.
  const { validate, reject, cancel, busy } = useSupervisedActionMutations(currentId);

  const summary = useMemo(() => {
    const pending = rows.filter((row) => ["prepared", "proposed", "pending", "requires_approval", "awaiting_approval"].includes(String(row.status || row.state || ""))).length;
    const executed = rows.filter((row) => ["executed", "completed"].includes(String(row.status || row.state || ""))).length;
    const failed = rows.filter((row) => ["failed", "validation_failed", "dry_run_failed"].includes(String(row.status || row.state || ""))).length;
    return { pending, executed, failed };
  }, [rows]);

  return (
    <main className="min-h-screen bg-background p-4 md:p-6" data-testid="supervised-actions-page">
      <div className="mx-auto flex max-w-7xl flex-col gap-4">
        <header className="flex flex-col gap-3 border-b pb-4 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase text-primary">OMEGA</p>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight">Acciones Supervisadas</h1>
            <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
              Validación y seguimiento de acciones preparadas por la consola.
            </p>
          </div>
          <button
            type="button"
            onClick={refresh}
            className="inline-flex min-h-[40px] items-center justify-center gap-2 rounded-md border px-3 text-sm font-medium hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <RefreshCw aria-hidden className={cn("h-4 w-4", actions.isLoading && "animate-spin")} />
            Actualizar
          </button>
        </header>

        <section className="grid gap-3 md:grid-cols-3" aria-label="Resumen">
          <SummaryTile label="Pendientes" value={summary.pending} />
          <SummaryTile label="Ejecutadas" value={summary.executed} />
          <SummaryTile label="Con revisión" value={summary.failed} />
        </section>

        <div className="grid gap-4 lg:grid-cols-[380px_minmax(0,1fr)]">
          <section className="rounded-md border bg-card p-3">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h2 className="text-sm font-semibold uppercase text-muted-foreground">Cola de acciones</h2>
              <span className="rounded-md border px-2 py-1 text-xs text-muted-foreground">{rows.length}</span>
            </div>
            {actions.isLoading ? (
              <div role="status" className="flex min-h-[220px] items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
                Cargando
              </div>
            ) : actions.error ? (
              <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                No se pudieron cargar las acciones.
              </div>
            ) : rows.length === 0 ? (
              <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
                Sin acciones pendientes.
              </div>
            ) : (
              <div className="space-y-2">
                {rows.map((row) => {
                  const id = actionId(row);
                  const active = id === currentId;
                  return (
                    <button
                      key={id}
                      type="button"
                      onClick={() => setSelectedId(id)}
                      className={cn(
                        "w-full rounded-md border p-3 text-left hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        active && "border-primary bg-primary/5",
                      )}
                    >
                      <div className="font-medium">{actionTitle(row)}</div>
                      <div className="mt-1 text-sm text-muted-foreground">
                        {stateLabel(String(row.status || row.state || ""))} · {shortDate(row.created_at)}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </section>

          <section className="rounded-md border bg-card p-4">
            {activeAction ? (
              <div className="space-y-4">
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <div>
                    <div className="flex items-center gap-2 text-xs font-semibold uppercase text-muted-foreground">
                      <ShieldCheck aria-hidden className="h-4 w-4 text-primary" />
                      Detalle
                    </div>
                    <h2 className="mt-1 text-xl font-semibold">{actionTitle(activeAction)}</h2>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {stateLabel(String(activeAction.status || activeAction.state || ""))} · {shortDate(activeAction.updated_at || activeAction.created_at)}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <ActionButton label="Validar" icon={ClipboardCheck} disabled={busy || !currentId || terminal} onClick={validate.run} />
                    <ActionButton label="Rechazar" icon={XCircle} disabled={busy || !currentId || terminal} onClick={reject.run} />
                    <ActionButton label="Cancelar" icon={Ban} disabled={busy || !currentId || terminal} onClick={cancel.run} />
                  </div>
                </div>

                <p role="note" className="flex items-start gap-2 rounded-md border border-primary/30 bg-primary/5 p-3 text-sm text-muted-foreground">
                  <Info aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                  La ejecución y la aprobación no están disponibles desde esta consola: las acciones operan en modo supervisado de solo preparación (preview).
                </p>

                {selected.isLoading ? (
                  <div role="status" className="flex min-h-[220px] items-center justify-center gap-2 text-sm text-muted-foreground">
                    <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
                    Cargando detalle
                  </div>
                ) : null}

                <div className="grid gap-3 md:grid-cols-2">
                  {/* Procedencia veraz: lo ausente se declara, nunca se inventa. */}
                  <DetailBox label="Fuente" value={activeAction.source_type ? String(activeAction.source_type) : "Fuente no informada"} />
                  <DetailBox label="Tipo" value={activeAction.action_type ? String(activeAction.action_type) : "Tipo no informado"} />
                  <DetailBox label="Creada" value={shortDate(activeAction.created_at)} />
                  <DetailBox label="Vence" value={shortDate(activeAction.expires_at)} />
                </div>

                {activeAction.last_error ? (
                  <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                    {activeAction.last_error}
                  </div>
                ) : null}

                <div className="rounded-md border bg-background p-3">
                  <div className="mb-2 text-xs font-semibold uppercase text-muted-foreground">Resumen técnico seguro</div>
                  <pre className="max-h-[320px] overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">
                    {JSON.stringify({
                      id: currentId,
                      estado: stateLabel(String(activeAction.status || activeAction.state || "")),
                      metadata: activeAction.metadata || {},
                    }, null, 2)}
                  </pre>
                </div>
              </div>
            ) : (
              <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
                Selecciona una acción para revisar su detalle.
              </div>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}

function SummaryTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border bg-card p-3">
      <div className="text-xs font-medium uppercase text-muted-foreground">{label}</div>
      <div className="mt-2 text-2xl font-semibold">{value}</div>
    </div>
  );
}

function DetailBox({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border bg-background p-3">
      <div className="text-xs font-medium uppercase text-muted-foreground">{label}</div>
      <div className="mt-1 text-sm">{value}</div>
    </div>
  );
}

function ActionButton({
  label,
  icon: Icon,
  disabled,
  primary,
  onClick,
}: {
  label: string;
  icon: LucideIcon;
  disabled?: boolean;
  primary?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "inline-flex min-h-[40px] items-center justify-center gap-2 rounded-md px-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
        primary ? "bg-primary text-primary-foreground hover:bg-primary/90" : "border hover:bg-accent/10",
      )}
    >
      <Icon aria-hidden className="h-4 w-4" />
      {label}
    </button>
  );
}
