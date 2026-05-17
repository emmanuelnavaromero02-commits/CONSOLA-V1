"use client";

import { useEffect, useRef } from "react";

import type { PendingAction } from "@/lib/copilot/types";

interface Props {
  open:        boolean;
  /** ID of the assistant message that produced pending_actions. */
  messageId:   string;
  pending:     PendingAction[];
  onApprove:   () => void | Promise<void>;
  onCancel:    () => void;
  submitting?: boolean;
}

/**
 * v1.44.4 Task A — destructive-action approval gate.
 *
 * The backend's run_turn flags ``requires_approval: true``
 * whenever the assistant proposes a tool whose
 * destructive-action policy demands confirmation. The user
 * sees ONE consolidated dialog summarising every queued
 * operation; clicking "Sí, ejecutar" calls
 * POST /api/copilot/conversations/{cid}/approve/{mid} which
 * runs the captured tools server-side.
 *
 * Accessibility:
 *   - dialog role + aria-modal,
 *   - aria-labelledby points at the title,
 *   - focus traps to the cancel button on open (safer default
 *     than "approve"),
 *   - Escape closes via onCancel,
 *   - Click outside the panel closes via the backdrop.
 *
 * Built as a native <dialog> shape instead of a Radix primitive
 * to keep the dependency surface small. shadcn/Radix can be
 * swapped in cleanly later — the component is purely
 * presentational.
 */
export function ApprovalGateDialog({
  open,
  pending,
  onApprove,
  onCancel,
  submitting,
}: Props) {
  const cancelRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    cancelRef.current?.focus();

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="approval-gate-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div className="w-full max-w-md rounded-lg border bg-card p-6 shadow-lg">
        <h2
          id="approval-gate-title"
          className="text-lg font-semibold tracking-tight"
        >
          ⚠️ Confirma esta acción
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          El copiloto propone ejecutar {pending.length === 1
            ? "una operación"
            : `${pending.length} operaciones`}{" "}
          que cambian datos. Esta acción NO se puede deshacer.
        </p>

        <ul
          aria-label="Operaciones pendientes"
          className="mt-4 space-y-2 text-sm"
        >
          {pending.map((p, i) => (
            <li
              key={i}
              className="rounded-md border bg-muted/40 px-3 py-2"
            >
              <p className="font-mono text-xs text-foreground">
                {String(p.tool ?? "(sin nombre)")}
              </p>
              {p.rationale ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  {String(p.rationale)}
                </p>
              ) : null}
            </li>
          ))}
        </ul>

        <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-4 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
          >
            Cancelar
          </button>
          <button
            type="button"
            onClick={() => void onApprove()}
            disabled={submitting}
            className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-destructive px-4 text-sm font-medium text-destructive-foreground hover:bg-destructive/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40 disabled:pointer-events-none disabled:opacity-60"
          >
            {submitting ? "Ejecutando…" : "Sí, ejecutar"}
          </button>
        </div>
      </div>
    </div>
  );
}
