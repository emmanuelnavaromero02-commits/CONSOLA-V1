"use client";

import { AlertTriangle, CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import type { ReactNode } from "react";

import {
  EXTRACTION_PHASES,
  EXTRACTION_STEP_LABELS,
  phaseCopy,
  type ExtractionProgressRun,
} from "@/lib/monitor/extraction-progress";
import { cn } from "@/lib/utils";

import { StatusPill } from "./StatusPill";

type StepState = "done" | "current" | "failed" | "pending";

function stepState(run: ExtractionProgressRun | undefined, index: number): StepState {
  if (!run) return "pending";
  const current = run.phase_index;
  if (index < current) return "done";
  if (index > current) return "pending";
  if (run.outcome === "failed") return "failed";
  if (run.terminal && run.phase === "ready") return "done";
  return "current";
}

function StepIcon({ state }: { state: StepState }) {
  if (state === "done") return <CheckCircle2 aria-hidden className="h-4 w-4 text-success" />;
  if (state === "failed") return <XCircle aria-hidden className="h-4 w-4 text-destructive" />;
  if (state === "current") return <Loader2 aria-hidden className="h-4 w-4 animate-spin text-primary" />;
  return <Circle aria-hidden className="h-4 w-4 text-muted-foreground" />;
}

const STEP_STATE_LABEL: Record<StepState, string> = {
  done: "completado",
  current: "en curso",
  failed: "con error",
  pending: "pendiente",
};

export function ExtractionProgressCard({
  title,
  runId,
  run,
  loading = false,
  errorMessage = null,
  followStopped = false,
  automationMessage = null,
  notice = null,
  onOpenStuckRuns,
  details,
  testId = "extraction-progress-card",
}: {
  title: string;
  runId: string | null;
  run?: ExtractionProgressRun;
  loading?: boolean;
  errorMessage?: string | null;
  followStopped?: boolean;
  automationMessage?: string | null;
  notice?: string | null;
  onOpenStuckRuns?: () => void;
  details?: ReactNode;
  testId?: string;
}) {
  const copy = phaseCopy(run);
  const statusForPill = run?.status ?? (loading ? "queued" : undefined);
  return (
    <section
      data-testid={testId}
      aria-label={title}
      className={cn(
        "space-y-3 rounded-md border bg-card p-3",
        copy.tone === "error" && "border-destructive/40",
        copy.tone === "warning" && "border-warning/40",
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">{title}</p>
        {statusForPill ? <StatusPill status={statusForPill} /> : null}
      </div>
      <p className="text-xs text-muted-foreground">
        Corrida <span className="break-all font-mono">{runId ?? "sin identificador"}</span>
      </p>

      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Fases de la extracción">
        {EXTRACTION_PHASES.map((phase, position) => {
          const state = stepState(run, position + 1);
          return (
            <li
              key={phase}
              aria-current={state === "current" ? "step" : undefined}
              className={cn(
                "flex items-center gap-2 rounded-md border px-2 py-1 text-xs",
                state === "current" && "border-primary/40 bg-primary/5 font-medium",
                state === "done" && "text-foreground",
                state === "failed" && "border-destructive/40 text-destructive",
                state === "pending" && "text-muted-foreground",
              )}
            >
              <StepIcon state={state} />
              <span>
                {EXTRACTION_STEP_LABELS[phase]}
                <span className="sr-only"> ({STEP_STATE_LABEL[state]})</span>
              </span>
            </li>
          );
        })}
      </ol>

      <div aria-live="polite" className="space-y-1">
        <p
          data-testid="extraction-progress-copy"
          className={cn(
            "text-sm",
            copy.tone === "error" && "text-destructive",
            copy.tone === "warning" && "text-warning",
            copy.tone === "success" && "text-success",
          )}
        >
          {copy.title}
        </p>
        {copy.detail ? <p className="break-words text-xs text-muted-foreground">{copy.detail}</p> : null}
      </div>

      {automationMessage ? <p className="text-xs text-muted-foreground">{automationMessage}</p> : null}
      {notice ? <p className="text-xs text-muted-foreground">{notice}</p> : null}
      {run?.recovered ? (
        <p className="text-xs text-muted-foreground">La plataforma cerró esta corrida tras verificar su estado en Airflow.</p>
      ) : null}
      {run?.stalled && onOpenStuckRuns ? (
        <button
          type="button"
          onClick={onOpenStuckRuns}
          className="inline-flex min-h-[36px] items-center gap-2 rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <AlertTriangle aria-hidden className="h-4 w-4 text-warning" /> Revisar corridas atascadas
        </button>
      ) : null}
      {errorMessage ? <p className="break-words text-xs text-destructive">{errorMessage}</p> : null}
      {followStopped && !run?.terminal ? (
        <p className="text-xs text-muted-foreground">
          Seguimiento detenido tras 30 minutos; revisa el monitor de pipelines.
        </p>
      ) : null}

      {details ? (
        <details className="text-xs">
          <summary className="cursor-pointer select-none text-muted-foreground">Detalles técnicos</summary>
          <div className="mt-2 space-y-2">{details}</div>
        </details>
      ) : null}
    </section>
  );
}
