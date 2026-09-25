"use client";

import { useEffect, useRef, useState } from "react";

import type { PendingAction } from "@/lib/copilot/types";

interface Props {
  open:        boolean;
  messageId:   string;
  pending:     PendingAction[];
  onApprove:   () => void | Promise<void>;
  onCancel:    () => void;
  submitting?: boolean;
  error?:      string | null;
}

function PendingActionDetail({ action }: { action: PendingAction }) {
  const [expanded, setExpanded] = useState(false);
  const hasArgs = action.args && Object.keys(action.args).length > 0;
  return (
    <li className="rounded-md border bg-muted/40 px-3 py-2">
      <div className="flex items-baseline justify-between gap-3">
        <p className="font-mono text-xs text-foreground">
          {String(action.tool ?? "(sin nombre)")}
        </p>
        {action.risk_level ? (
          <span className="inline-flex items-center rounded-full bg-destructive/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-destructive">
            {action.risk_level}
          </span>
        ) : null}
      </div>
      {hasArgs ? (
        <button
          type="button"
          onClick={() => setExpanded((s) => !s)}
          aria-expanded={expanded}
          className="mt-1 text-[11px] font-medium text-muted-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:underline"
        >
          {expanded ? "Ocultar detalles" : "Ver detalles"}
        </button>
      ) : null}
      {expanded && hasArgs ? (
        <pre className="mt-1 max-h-40 overflow-auto rounded bg-background p-2 text-[11px]">
{JSON.stringify(action.args, null, 2)}
        </pre>
      ) : null}
    </li>
  );
}


export function ApprovalGateDialog({
  open,
  pending,
  onApprove,
  onCancel,
  submitting,
  error,
}: Props) {
  const cancelRef     = useRef<HTMLButtonElement | null>(null);
  const dialogRef     = useRef<HTMLDivElement | null>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const submittingRef = useRef(Boolean(submitting));

  useEffect(() => {
    submittingRef.current = Boolean(submitting);
  }, [submitting]);

  useEffect(() => {
    if (!open) return;

    previousFocus.current = (document.activeElement instanceof HTMLElement)
      ? document.activeElement
      : null;

    cancelRef.current?.focus();

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        if (!submittingRef.current) onCancel();
        return;
      }
      if (e.key !== "Tab") return;
      if (submittingRef.current) {
        e.preventDefault();
        dialogRef.current?.focus();
        return;
      }
      const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      ) ?? [])];
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previousFocus.current?.focus();
    };
  }, [open, onCancel]);

  useEffect(() => {
    if (open && submitting) dialogRef.current?.focus();
  }, [open, submitting]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="approval-gate-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      <div
        className="absolute inset-0 bg-black/50"
        onClick={() => {
          if (!submitting) onCancel();
        }}
        aria-hidden
      />
      <div
        ref={dialogRef}
        tabIndex={-1}
        className="relative w-full max-w-md rounded-lg border bg-card p-6 shadow-lg focus-visible:outline-none"
      >
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
          {pending.map((p) => (
            <PendingActionDetail
              key={p.approval_key ?? `${p.tool}-${JSON.stringify(p.args ?? {})}`}
              action={p}
            />
          ))}
        </ul>

        {error ? (
          <div
            role="alert"
            className="mt-4 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm"
          >
            <p className="font-medium text-destructive">
              No se pudo ejecutar la acción.
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {error}
            </p>
          </div>
        ) : null}

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
            {submitting ? "Ejecutando…" : error ? "Reintentar" : "Sí, ejecutar"}
          </button>
        </div>
      </div>
    </div>
  );
}
