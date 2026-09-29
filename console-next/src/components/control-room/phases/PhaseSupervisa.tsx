"use client";

import { useQuery } from "@tanstack/react-query";
import { Inbox, Loader2 } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { listDecisions, type Decision } from "@/lib/admin-surfaces";
import { cn } from "@/lib/utils";

import { SupervisedActionsQueue } from "./SupervisedActionsQueue";

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

function TicketsTable({ decisions, now }: { decisions: Decision[]; now: Date }) {
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <thead>
          <tr className="border-b bg-muted/40 text-left text-xs font-semibold uppercase text-muted-foreground">
            <th scope="col" className="px-3 py-2">Título</th>
            <th scope="col" className="px-3 py-2">Responsable</th>
            <th scope="col" className="px-3 py-2">Días restantes</th>
            <th scope="col" className="px-3 py-2">Impacto estimado</th>
          </tr>
        </thead>
        <tbody>
          {decisions.map((decision) => {
            const days = remainingDays(decision.commitment_date, now);
            const impact = decisionImpact(decision.kpis);
            return (
              <tr
                key={decision.id}
                className={cn("border-b last:border-b-0", days.overdue && "bg-destructive/5")}
              >
                <td className="px-3 py-2 font-medium">{decision.title}</td>
                <td className="px-3 py-2 text-muted-foreground">{responsible(decision)}</td>
                <td className={cn("px-3 py-2", days.overdue ? "font-semibold text-destructive" : "text-muted-foreground")}>
                  {days.label}
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
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TicketsSection() {
  const decisions = useQuery({
    queryKey: ["decisions", "open"],
    queryFn: () => listDecisions("open"),
    staleTime: 20_000,
  });
  const now = new Date();

  return (
    <section aria-label="Tickets en curso" className="flex flex-col gap-3">
      <div className="min-w-0">
        <h2 className="text-lg font-semibold tracking-tight">Tickets en curso</h2>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Decisiones abiertas con su compromiso, responsable e impacto estimado.
        </p>
      </div>
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
        <EmptyState icon={Inbox} size="sm" title="Sin tickets en curso." className="rounded-md border border-dashed" />
      ) : (
        <TicketsTable decisions={decisions.data ?? []} now={now} />
      )}
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
