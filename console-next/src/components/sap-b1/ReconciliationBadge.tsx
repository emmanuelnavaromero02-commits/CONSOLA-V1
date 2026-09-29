"use client";

import { useSapB1View } from "@/lib/sap-b1/hooks";
import { formatCount, formatPct } from "@/lib/sap-b1/present";
import type { FinanceReconciliationKpi, Num } from "@/lib/sap-b1/types";

import { Pill, type PillTone } from "./ui";

export const DEFAULT_RECONCILIATION_TOLERANCE_PCT = 1;

export interface ReconciliationBadgeState {
  tone: PillTone;
  label: string;
  detail: string | null;
}

function finite(value: Num): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function reconciliationBadgeState(
  metric: FinanceReconciliationKpi | null | undefined,
): ReconciliationBadgeState {
  const rows = metric?.rows;
  if (!metric || !finite(rows) || rows <= 0) {
    return { tone: "neutral", label: "Sin corrida de Finanzas cargada", detail: null };
  }
  const within = finite(metric.within) ? metric.within : 0;
  const okPct = finite(metric.within_pct) ? metric.within_pct : (100 * within) / rows;
  const tolerance =
    finite(metric.tolerance_pct) && metric.tolerance_pct > 0
      ? metric.tolerance_pct
      : DEFAULT_RECONCILIATION_TOLERANCE_PCT;
  const outsidePct = 100 - okPct;
  return {
    tone: outsidePct < tolerance ? "good" : "warning",
    label: `Conciliación ${formatPct(okPct)}`,
    detail: `${formatCount(within)} de ${formatCount(rows)} filas de Finanzas dentro de la tolerancia de ${formatPct(tolerance)}`,
  };
}

export function ReconciliationBadge() {
  const view = useSapB1View("sap_b1_margin_kpis");
  const metric = view.data?.metrics?.reconciliacion_finanzas;
  const state = view.data ? reconciliationBadgeState(metric) : null;
  return (
    <div
      role="status"
      aria-label="Conciliación con Finanzas"
      className="flex flex-wrap items-center gap-2 rounded-lg border bg-card p-3 text-sm shadow-sm dark:border-sky-400/20 dark:bg-[#081423]"
    >
      <span className="font-medium text-foreground dark:text-white">Conciliación con Finanzas</span>
      {view.isPending ? <span className="text-muted-foreground">Calculando…</span> : null}
      {view.isError ? <span className="text-muted-foreground">No disponible por ahora.</span> : null}
      {state ? <Pill tone={state.tone}>{state.label}</Pill> : null}
      {state?.detail ? <span className="text-muted-foreground">{state.detail}</span> : null}
      {metric?.period ? <span className="text-xs text-muted-foreground">Periodo: {metric.period}</span> : null}
    </div>
  );
}
