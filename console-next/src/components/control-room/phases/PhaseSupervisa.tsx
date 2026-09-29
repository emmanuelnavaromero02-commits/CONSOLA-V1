"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Inbox, Loader2, RotateCcw, XCircle } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import {
  addDecisionAction,
  getMeAccess,
  listDecisions,
  updateDecision,
  type Decision,
} from "@/lib/admin-surfaces";
import { cn } from "@/lib/utils";

import { SupervisedActionsQueue } from "./SupervisedActionsQueue";

type TicketFilter = "open" | "closed";
type TicketOutcome = "achieved" | "not_achieved";

const OUTCOME_LABELS: Record<TicketOutcome, string> = {
  achieved: "Cumplida",
  not_achieved: "No cumplida",
};

export interface RemainingDays {
  label: string;
  overdue: boolean;
}

export function remainingDays(
  commitmentDate: string | null | undefined,
  now: Date,
): RemainingDays {
  if (!commitmentDate) return { label: "Sin fecha compromiso", overdue: false };
  const parsed = new Date(`${String(commitmentDate).slice(0, 10)}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return { label: "Sin fecha compromiso", overdue: false };
  }
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const days = Math.round((parsed.getTime() - today.getTime()) / 86_400_000);
  if (days > 1) return { label: `${days} días restantes`, overdue: false };
  if (days === 1) return { label: "1 día restante", overdue: false };
  if (days === 0) return { label: "Vence hoy", overdue: false };
  if (days === -1) return { label: "Vencido hace 1 día", overdue: true };
  return { label: `Vencido hace ${-days} días`, overdue: true };
}

export interface DecisionImpactView {
  text: string;
  rule: string | null;
}

function formatMoney(value: number, currency: string | null): string {
  if (currency) {
    try {
      return new Intl.NumberFormat("es-MX", {
        style: "currency",
        currency,
        currencyDisplay: "code",
      }).format(value);
    } catch {
      return `${value} ${currency}`;
    }
  }
  return String(value);
}

export function decisionImpact(kpis: unknown): DecisionImpactView | null {
  if (!Array.isArray(kpis)) return null;
  for (const entry of kpis) {
    if (!entry || typeof entry !== "object") continue;
    const record = entry as Record<string, unknown>;
    const snapshot = record.impacto_estimado;
    if (!snapshot || typeof snapshot !== "object") continue;
    const details = snapshot as Record<string, unknown>;
    const value = details.valor;
    if (typeof value !== "number" || !Number.isFinite(value)) continue;
    const currency = typeof details.moneda === "string" && details.moneda ? details.moneda : null;
    const rule = typeof record.regla === "string" && record.regla ? record.regla : null;
    return { text: formatMoney(value, currency), rule };
  }
  return null;
}

function responsible(decision: Decision): string {
  const value = decision.created_by;
  return typeof value === "string" && value.trim() ? value : "Sin información";
}

function outcomeCopy(outcome?: string | null): string {
  if (outcome === "achieved" || outcome === "not_achieved") {
    return OUTCOME_LABELS[outcome];
  }
  return "Sin resultado";
}

interface TicketDialogState {
  kind: "close" | "reopen";
  decision: Decision;
}

interface TicketNotice {
  kind: "success" | "error";
  text: string;
}

function TicketNoticeLine({ notice }: { notice: TicketNotice | null }) {
  if (!notice) return null;
  if (notice.kind === "error") {
    return (
      <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-2 text-sm text-destructive">
        {notice.text}
      </p>
    );
  }
  return (
    <p role="status" className="rounded-md border border-success/40 bg-success/10 p-2 text-sm text-foreground">
      {notice.text}
    </p>
  );
}

function TicketDialog({
  state,
  outcome,
  note,
  saving,
  onOutcome,
  onNote,
  onConfirm,
  onCancel,
}: {
  state: TicketDialogState;
  outcome: TicketOutcome | null;
  note: string;
  saving: boolean;
  onOutcome: (value: TicketOutcome) => void;
  onNote: (value: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const closing = state.kind === "close";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={closing ? "Cerrar con resultado" : "Reabrir decisión"}
        className="w-full max-w-md rounded-lg border bg-card p-5 shadow-lg"
      >
        <h3 className="text-lg font-semibold">
          {closing ? "Cerrar con resultado" : "Reabrir decisión"}
        </h3>
        <p className="mt-1 break-words text-sm text-muted-foreground">{state.decision.title}</p>

        {closing ? (
          <div className="mt-4 space-y-3">
            <fieldset>
              <legend className="text-xs font-medium uppercase text-muted-foreground">
                Resultado
              </legend>
              <div className="mt-2 flex flex-wrap gap-3">
                {(Object.keys(OUTCOME_LABELS) as TicketOutcome[]).map((value) => (
                  <label key={value} className="inline-flex min-h-[40px] items-center gap-2 rounded-md border px-3 text-sm">
                    <input
                      type="radio"
                      name="ticket-outcome"
                      value={value}
                      checked={outcome === value}
                      onChange={() => onOutcome(value)}
                    />
                    {OUTCOME_LABELS[value]}
                  </label>
                ))}
              </div>
            </fieldset>
            <label className="block text-sm">
              <span className="text-xs font-medium uppercase text-muted-foreground">
                Nota (opcional)
              </span>
              <textarea
                value={note}
                onChange={(event) => onNote(event.target.value)}
                className="mt-1 min-h-[90px] w-full resize-y rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
          </div>
        ) : (
          <p className="mt-4 text-sm text-muted-foreground">
            La decisión vuelve a estado abierto y pierde su resultado registrado.
          </p>
        )}

        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={saving}
            className="inline-flex min-h-[40px] items-center rounded-md border bg-background px-3 text-sm font-medium hover:bg-muted disabled:opacity-50"
          >
            Cancelar
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={saving || (closing && outcome === null)}
            className="inline-flex min-h-[40px] items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {saving ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : null}
            {closing ? "Confirmar cierre" : "Confirmar reapertura"}
          </button>
        </div>
      </div>
    </div>
  );
}

function TicketsTable({
  decisions,
  filter,
  now,
  canWrite,
  onClose,
  onReopen,
}: {
  decisions: Decision[];
  filter: TicketFilter;
  now: Date;
  canWrite: boolean;
  onClose: (decision: Decision) => void;
  onReopen: (decision: Decision) => void;
}) {
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <thead>
          <tr className="border-b bg-muted/40 text-left text-xs font-semibold uppercase text-muted-foreground">
            <th scope="col" className="px-3 py-2">Título</th>
            <th scope="col" className="px-3 py-2">Responsable</th>
            <th scope="col" className="px-3 py-2">
              {filter === "open" ? "Días restantes" : "Resultado"}
            </th>
            <th scope="col" className="px-3 py-2">Impacto estimado</th>
            {canWrite ? <th scope="col" className="px-3 py-2">Acciones</th> : null}
          </tr>
        </thead>
        <tbody>
          {decisions.map((decision) => {
            const days = remainingDays(decision.commitment_date, now);
            const overdue = filter === "open" && days.overdue;
            const impact = decisionImpact(decision.kpis);
            return (
              <tr
                key={decision.id}
                className={cn("border-b last:border-b-0", overdue && "bg-destructive/5")}
              >
                <td className="px-3 py-2 font-medium">{decision.title}</td>
                <td className="px-3 py-2 text-muted-foreground">{responsible(decision)}</td>
                <td className={cn("px-3 py-2", overdue ? "font-semibold text-destructive" : "text-muted-foreground")}>
                  {filter === "open" ? days.label : outcomeCopy(decision.outcome)}
                </td>
                <td className="px-3 py-2">
                  {impact ? (
                    <span className="block">
                      <span className="font-medium">{impact.text}</span>
                      {impact.rule ? (
                        <span className="mt-0.5 block text-xs text-muted-foreground">{impact.rule}</span>
                      ) : null}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">Sin información</span>
                  )}
                </td>
                {canWrite ? (
                  <td className="px-3 py-2">
                    {filter === "open" ? (
                      <button
                        type="button"
                        onClick={() => onClose(decision)}
                        className="inline-flex min-h-[36px] items-center gap-1.5 rounded-md border bg-background px-2.5 text-xs font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <CheckCircle2 aria-hidden className="h-3.5 w-3.5" />
                        Cerrar con resultado
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => onReopen(decision)}
                        className="inline-flex min-h-[36px] items-center gap-1.5 rounded-md border bg-background px-2.5 text-xs font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        <RotateCcw aria-hidden className="h-3.5 w-3.5" />
                        Reabrir
                      </button>
                    )}
                  </td>
                ) : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TicketsSection() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<TicketFilter>("open");
  const [dialog, setDialog] = useState<TicketDialogState | null>(null);
  const [outcome, setOutcome] = useState<TicketOutcome | null>(null);
  const [note, setNote] = useState("");
  const [notice, setNotice] = useState<TicketNotice | null>(null);

  const access = useQuery({
    queryKey: ["me", "access"],
    queryFn: getMeAccess,
    staleTime: 60_000,
  });
  const canWrite = (access.data?.permissions ?? []).includes("control_room.write");

  const decisions = useQuery({
    queryKey: ["decisions", filter],
    queryFn: () => listDecisions(filter),
    staleTime: 20_000,
  });
  const now = new Date();

  const mutation = useMutation({
    mutationFn: async (state: TicketDialogState) => {
      if (state.kind === "close") {
        if (outcome === null) throw new Error("Selecciona un resultado.");
        await updateDecision(state.decision.id, { status: "closed", outcome });
        const trimmed = note.trim();
        if (trimmed) {
          try {
            await addDecisionAction(state.decision.id, trimmed);
          } catch {
            return { partialNote: true };
          }
        }
        return { partialNote: false };
      }
      await updateDecision(state.decision.id, { status: "open", outcome: null });
      return { partialNote: false };
    },
    onSuccess: async (result, state) => {
      await queryClient.invalidateQueries({ queryKey: ["decisions"] });
      setDialog(null);
      setOutcome(null);
      setNote("");
      if (result.partialNote) {
        setNotice({
          kind: "error",
          text: "La decisión se cerró, pero la nota no se pudo registrar.",
        });
      } else {
        setNotice({
          kind: "success",
          text: state.kind === "close" ? "Decisión cerrada." : "Decisión reabierta.",
        });
      }
    },
    onError: (error) => {
      setNotice({
        kind: "error",
        text: error instanceof Error ? error.message : "No se pudo actualizar la decisión.",
      });
    },
  });

  const openDialog = (state: TicketDialogState) => {
    setNotice(null);
    setOutcome(null);
    setNote("");
    setDialog(state);
  };

  return (
    <section aria-label="Tickets en curso" className="flex flex-col gap-3">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold tracking-tight">Tickets en curso</h2>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Decisiones con su compromiso, responsable e impacto estimado.
          </p>
        </div>
        <div className="flex gap-2" role="group" aria-label="Filtro de tickets">
          {(["open", "closed"] as TicketFilter[]).map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={filter === value}
              onClick={() => setFilter(value)}
              className={cn(
                "inline-flex min-h-[40px] items-center rounded-md px-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                filter === value
                  ? "bg-primary text-primary-foreground"
                  : "border hover:bg-accent/10",
              )}
            >
              {value === "open" ? "Abiertos" : "Cerrados"}
            </button>
          ))}
        </div>
      </div>

      <TicketNoticeLine notice={notice} />

      {decisions.isLoading ? (
        <div role="status" className="flex min-h-[160px] items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
          Cargando
        </div>
      ) : decisions.error ? (
        <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          No se pudieron cargar los tickets.
        </div>
      ) : (decisions.data ?? []).length === 0 ? (
        <EmptyState
          icon={filter === "open" ? Inbox : XCircle}
          size="sm"
          title={filter === "open" ? "Sin tickets en curso." : "Sin tickets cerrados."}
          className="rounded-md border border-dashed"
        />
      ) : (
        <TicketsTable
          decisions={decisions.data ?? []}
          filter={filter}
          now={now}
          canWrite={canWrite}
          onClose={(decision) => openDialog({ kind: "close", decision })}
          onReopen={(decision) => openDialog({ kind: "reopen", decision })}
        />
      )}

      {dialog ? (
        <TicketDialog
          state={dialog}
          outcome={outcome}
          note={note}
          saving={mutation.isPending}
          onOutcome={setOutcome}
          onNote={setNote}
          onConfirm={() => mutation.mutate(dialog)}
          onCancel={() => {
            if (!mutation.isPending) setDialog(null);
          }}
        />
      ) : null}
    </section>
  );
}

export function PhaseSupervisa() {
  return (
    <main className="min-h-screen bg-background p-4 md:p-6" data-testid="phase-supervisa">
      <div className="mx-auto flex max-w-7xl flex-col gap-6">
        <header className="border-b pb-4">
          <p className="text-xs font-semibold uppercase text-primary">Control Room</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">Supervisa</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Seguimiento de compromisos abiertos y validación de acciones preparadas.
          </p>
        </header>
        <TicketsSection />
        <SupervisedActionsQueue />
      </div>
    </main>
  );
}
