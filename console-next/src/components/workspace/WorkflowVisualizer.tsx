"use client";

import { cn } from "@/lib/utils";
import type { Workflow, WorkflowStatus } from "@/lib/copilot/types";

interface Props {
  workflow: Workflow;
}

const STATUS_DOT: Record<WorkflowStatus, string> = {
  planning:          "bg-muted text-muted-foreground",
  running:           "bg-blue-500 text-blue-50",
  awaiting_approval: "bg-amber-500 text-amber-50",
  completed:         "bg-emerald-500 text-emerald-50",
  cancelled:         "bg-muted text-muted-foreground",
  failed:            "bg-destructive text-destructive-foreground",
};


const STATUS_LABEL: Record<WorkflowStatus, string> = {
  planning:          "Planeando",
  running:           "En curso",
  awaiting_approval: "Esperando aprobación",
  completed:         "Completado",
  cancelled:         "Cancelado",
  failed:            "Falló",
};


const STATUS_ICON: Record<WorkflowStatus, string> = {
  planning:          "○",
  running:           "◐",
  awaiting_approval: "⏸",
  completed:         "✓",
  cancelled:         "✕",
  failed:            "!",
};


/**
 * v1.44.4 Task A — multi-step workflow visualiser.
 *
 * Renders the steps the copilot's planner emits via
 * /api/copilot/workflow. The list is vertical with a status
 * dot, label, optional detail, and a connector line so the
 * operator can trace progress at a glance.
 *
 * Live status updates come from polling the workflow detail
 * endpoint via ``useWorkflow`` — the page passes the latest
 * Workflow object every render and this component is
 * pure-presentational.
 */
export function WorkflowVisualizer({ workflow }: Props) {
  return (
    <section
      aria-label={`Workflow ${workflow.title ?? workflow.id}`}
      className="rounded-lg border bg-card p-4 shadow-sm"
    >
      <header className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold tracking-tight">
          {workflow.title ?? "Workflow"}
        </h3>
        <span
          className={cn(
            "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
            STATUS_DOT[workflow.status],
          )}
        >
          {STATUS_LABEL[workflow.status]}
        </span>
      </header>

      <ol className="relative space-y-2 border-l border-border pl-5">
        {workflow.steps.map((s) => (
          <li key={s.id} className="relative">
            <span
              className={cn(
                "absolute -left-[1.4rem] top-0.5 inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold",
                STATUS_DOT[s.status],
              )}
              aria-hidden
            >
              {STATUS_ICON[s.status]}
            </span>
            <p className="text-sm font-medium">{s.title}</p>
            {s.detail ? (
              <p className="text-xs text-muted-foreground">{s.detail}</p>
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}
