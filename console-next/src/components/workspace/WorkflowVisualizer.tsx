"use client";

import { cn } from "@/lib/utils";
import type {
  Workflow,
  WorkflowDetailResponse,
  WorkflowRunStatus,
  WorkflowStep,
  WorkflowStepStatus,
} from "@/lib/copilot/types";

interface Props {
  detail: WorkflowDetailResponse;
}

const RUN_STATUS_BADGE: Record<WorkflowRunStatus, string> = {
  planning:   "bg-muted text-muted-foreground",
  running:    "bg-blue-500 text-white",
  completed:  "bg-emerald-500 text-white",
  cancelled:  "bg-muted text-muted-foreground",
  failed:     "bg-destructive text-destructive-foreground",
};


const RUN_STATUS_LABEL: Record<WorkflowRunStatus, string> = {
  planning:   "Planeando",
  running:    "En curso",
  completed:  "Completado",
  cancelled:  "Cancelado",
  failed:     "Falló",
};


const STEP_STATUS_DOT: Record<WorkflowStepStatus, string> = {
  pending:   "bg-muted text-muted-foreground",
  running:   "bg-blue-500 text-white",
  completed: "bg-emerald-500 text-white",
  failed:    "bg-destructive text-destructive-foreground",
  skipped:   "bg-muted text-muted-foreground",
};


const STEP_STATUS_ICON: Record<WorkflowStepStatus, string> = {
  pending:   "○",
  running:   "◐",
  completed: "✓",
  failed:    "!",
  skipped:   "—",
};


const STEP_STATUS_LABEL: Record<WorkflowStepStatus, string> = {
  pending:   "Pendiente",
  running:   "En curso",
  completed: "Completado",
  failed:    "Falló",
  skipped:   "Omitido",
};


function StepRow({ step }: { step: WorkflowStep }) {
  return (
    <li className="relative">
      <span
        className={cn(
          "absolute -left-[1.4rem] top-0.5 inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold",
          STEP_STATUS_DOT[step.status],
        )}
        aria-label={STEP_STATUS_LABEL[step.status]}
      >
        {STEP_STATUS_ICON[step.status]}
      </span>
      <p className="text-sm font-medium">{step.description}</p>
      {step.tool ? (
        <p className="font-mono text-[11px] text-muted-foreground">
          {step.tool}
        </p>
      ) : null}
    </li>
  );
}


export function WorkflowVisualizer({ detail }: Props) {
  const wf: Workflow = detail.workflow;
  const steps: WorkflowStep[] = [...(detail.steps ?? [])].sort(
    (a, b) => a.step_idx - b.step_idx,
  );

  return (
    <section
      aria-label={`Workflow ${wf.intent ?? wf.id}`}
      className="rounded-lg border bg-card p-4 shadow-sm"
    >
      <header className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold tracking-tight">
          {wf.intent ?? "Workflow"}
        </h3>
        <span
          className={cn(
            "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
            RUN_STATUS_BADGE[wf.status],
          )}
        >
          {RUN_STATUS_LABEL[wf.status]}
        </span>
      </header>

      {steps.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          Aún no hay pasos planeados.
        </p>
      ) : (
        <ol className="relative space-y-2 border-l border-border pl-5">
          {steps.map((s) => (
            <StepRow key={s.step_idx} step={s} />
          ))}
        </ol>
      )}
    </section>
  );
}
