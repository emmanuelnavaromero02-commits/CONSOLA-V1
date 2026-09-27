"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog, Notice, Spinner } from "@/components/studio/ui";
import { isApiError } from "@/lib/api";
import { recoverStuckRuns, type StuckRunRecovery } from "@/lib/monitor/extraction-progress";
import { pipelineErrorDetail } from "@/lib/pipeline-error-copy";

const MAX_LISTED = 8;

function errorCopy(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "No se pudo revisar las corridas atascadas.";
}

function CountsSummary({ plan }: { plan: StuckRunRecovery }) {
  const { counts } = plan;
  const items: Array<[string, number]> = [
    ["Revisadas", counts.candidates],
    ["Por cerrar o sincronizar", counts.recoverable],
    ["Siguen en curso", counts.live],
    ["Sin verificar en Airflow", counts.unverifiable],
  ];
  if (plan.mode === "applied") {
    items.push(
      ["Cerradas", counts.recovered],
      ["Sincronizadas", counts.synced_terminal],
      ["Detenidas en Airflow", counts.airflow_neutralized],
      ["Sin cambios por conflicto", counts.conflicts],
    );
  }
  return (
    <dl className="grid grid-cols-2 gap-2 text-xs" data-testid="stuck-runs-counts">
      {items.map(([label, value]) => (
        <div key={label} className="rounded-md border bg-muted/20 p-2">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="text-base font-semibold">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

type StuckRunsDialogProps = {
  open: boolean;
  cartridge?: string | null;
  dagId?: string | null;
  onClose: () => void;
  onRecovered?: (result: StuckRunRecovery) => void;
};

export function StuckRunsDialog(props: StuckRunsDialogProps) {
  if (!props.open) return null;
  return <StuckRunsReview key={`${props.cartridge ?? ""}:${props.dagId ?? ""}`} {...props} />;
}

function StuckRunsReview({ cartridge, dagId, onClose, onRecovered }: StuckRunsDialogProps) {
  const [plan, setPlan] = useState<StuckRunRecovery | null>(null);
  const [loading, setLoading] = useState(true);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const scope = { cartridge: cartridge || undefined, dag_id: dagId || undefined };

  async function review(message: string | null = null) {
    setLoading(true);
    setError(null);
    setNotice(message);
    try {
      setPlan(await recoverStuckRuns(scope));
    } catch (reviewError) {
      setPlan(null);
      setError(errorCopy(reviewError));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    recoverStuckRuns({ cartridge: cartridge || undefined, dag_id: dagId || undefined }).then(
      (result) => {
        if (!active) return;
        setPlan(result);
        setLoading(false);
      },
      (reviewError: unknown) => {
        if (!active) return;
        setError(errorCopy(reviewError));
        setLoading(false);
      },
    );
    return () => {
      active = false;
    };
  }, [cartridge, dagId]);

  async function apply() {
    if (!plan || plan.mode !== "dry_run") return;
    setApplying(true);
    setError(null);
    try {
      const result = await recoverStuckRuns({ ...scope, apply: true, plan_digest: plan.plan_digest });
      setPlan(result);
      setNotice(null);
      onRecovered?.(result);
    } catch (applyError) {
      const detail = isApiError(applyError) ? pipelineErrorDetail(applyError.data) : null;
      if (detail?.reason === "plan_changed") {
        await review(detail.copy);
      } else {
        setError(errorCopy(applyError));
      }
    } finally {
      setApplying(false);
    }
  }

  const actionable = (plan?.runs ?? []).filter((run) => run.action !== "none");
  const reviewing = plan?.mode === "dry_run";
  const confirmLabel = reviewing
    ? `Cerrar ${plan?.counts.recoverable ?? 0} ${plan?.counts.recoverable === 1 ? "corrida" : "corridas"}`
    : "Revisar de nuevo";

  return (
    <ConfirmDialog
      open
      testId="stuck-runs-dialog"
      title="Corridas atascadas"
      description="Se verifica cada corrida en Airflow antes de cerrarla. Nada cambia hasta que confirmes."
      confirmLabel={confirmLabel}
      pendingLabel="Aplicando…"
      pending={applying}
      confirmDisabled={loading || (reviewing && !plan?.counts.recoverable) || (!plan && !error)}
      onConfirm={() => (reviewing ? void apply() : void review())}
      onCancel={onClose}
    >
      {loading ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner /> Revisando corridas en Airflow…
        </p>
      ) : null}
      {notice ? <Notice tone="warning">{notice}</Notice> : null}
      {error ? <Notice tone="error">{error}</Notice> : null}
      {plan && !loading ? (
        <div className="space-y-3">
          <p aria-live="polite" className="text-sm" data-testid="stuck-runs-message">
            {plan.message_es}
          </p>
          <CountsSummary plan={plan} />
          {actionable.length ? (
            <ul className="space-y-1 text-xs" aria-label="Corridas incluidas">
              {actionable.slice(0, MAX_LISTED).map((run) => (
                <li key={run.run_id} className="rounded-md border p-2">
                  <span className="font-medium">{run.entity}</span>
                  {run.age_minutes != null ? ` · hace ${run.age_minutes} min` : ""}
                  <span className="block text-muted-foreground">{run.reason_es}</span>
                </li>
              ))}
              {actionable.length > MAX_LISTED ? (
                <li className="text-muted-foreground">y {actionable.length - MAX_LISTED} más.</li>
              ) : null}
            </ul>
          ) : null}
        </div>
      ) : null}
    </ConfirmDialog>
  );
}
