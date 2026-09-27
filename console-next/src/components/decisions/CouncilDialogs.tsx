"use client";

import { type ReactNode, useEffect, useId, useRef, useState } from "react";

import {
  normalizeReason,
  reasonHasHiddenCharacters,
  reasonVisibleLength,
} from "@/lib/control-room/use-control-room-experience-action";
import {
  DISCARD_REASON_LIMITS,
  discardReasonIsValid,
  type CouncilCommand,
  type CouncilPhase,
  type CouncilSelection,
} from "@/lib/decisions/use-action-council";

export const APPROVE_LABEL = "Aprobar e implementar";
export const DISCARD_LABEL = "Descartar con motivo";
export const RENEW_LABEL = "Renovar propuesta";
export const APPROVE_EXPLANATION =
  "Aprobar e implementar = aprobar la decisión y registrar una tarea de seguimiento interna; no modifica sistemas externos (ERP).";

export const COMMAND_COPY: Record<
  CouncilCommand,
  { title: string; description: string; confirm: string; submitting: string }
> = {
  approve: {
    title: APPROVE_LABEL,
    description: APPROVE_EXPLANATION,
    confirm: APPROVE_LABEL,
    submitting: "Aprobando…",
  },
  discard: {
    title: DISCARD_LABEL,
    description:
      "La propuesta se descarta y el hallazgo se archiva con tu motivo. Si tiene una decisión asociada, la decisión se cierra.",
    confirm: DISCARD_LABEL,
    submitting: "Descartando…",
  },
  renew: {
    title: RENEW_LABEL,
    description:
      "Se prepara de nuevo la tarea de seguimiento con los datos actuales; seguirá necesitando la aprobación de otra persona del equipo.",
    confirm: RENEW_LABEL,
    submitting: "Renovando…",
  },
};

function ResultBanner({
  role,
  className,
  children,
}: {
  role: "status" | "alert";
  className: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    ref.current?.scrollIntoView?.({ block: "nearest" });
  }, []);
  return (
    <div ref={ref} role={role} className={className}>
      {children}
    </div>
  );
}

export function CouncilResult({
  phase,
  message,
}: {
  phase: CouncilPhase;
  message: string | null;
}) {
  if (!message) return null;
  if (phase === "success") {
    return (
      <ResultBanner
        role="status"
        className="border-l-2 border-success bg-success/10 px-4 py-3 text-sm text-foreground"
      >
        <p>{message}</p>
      </ResultBanner>
    );
  }
  if (phase === "safe-error") {
    return (
      <ResultBanner
        role="alert"
        className="border-l-2 border-destructive bg-destructive/10 px-4 py-3 text-sm text-foreground"
      >
        {message}
      </ResultBanner>
    );
  }
  return null;
}

export function CouncilDialog({
  phase,
  selection,
  cancel,
  confirm,
}: {
  phase: CouncilPhase;
  selection: CouncilSelection;
  cancel: () => void;
  confirm: (reason?: string) => Promise<void>;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const reasonId = useId();
  const counterId = useId();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const reasonRef = useRef<HTMLTextAreaElement | null>(null);
  const phaseRef = useRef(phase);
  const [reason, setReason] = useState("");
  const submitting = phase === "submitting";
  const copy = COMMAND_COPY[selection.command];
  const needsReason = selection.command === "discard";
  const reasonLength = Array.from(normalizeReason(reason)).length;
  const reasonLetters = reasonVisibleLength(reason);
  const canConfirm = !submitting && (!needsReason || discardReasonIsValid(reason));
  const opener = selection.opener;

  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    (reasonRef.current ?? cancelRef.current)?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        if (phaseRef.current !== "submitting") cancel();
        return;
      }
      if (event.key !== "Tab") return;
      if (phaseRef.current === "submitting") {
        event.preventDefault();
        dialogRef.current?.focus();
        return;
      }
      const focusable = [
        ...(dialogRef.current?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
        ) ?? []),
      ];
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      if (opener?.isConnected) opener.focus();
    };
  }, [cancel, opener]);

  useEffect(() => {
    if (submitting) dialogRef.current?.focus();
  }, [submitting]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50" aria-hidden />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        aria-busy={submitting}
        tabIndex={-1}
        className="relative w-full max-w-lg rounded-lg border bg-card p-6 shadow-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <h2 id={titleId} className="text-lg font-semibold text-card-foreground">
          {copy.title}
        </h2>
        <p id={descriptionId} className="mt-2 text-sm text-muted-foreground">
          {copy.description}
        </p>
        <dl className="mt-5 text-sm">
          <dt className="font-medium text-muted-foreground">Propuesta</dt>
          <dd className="break-words text-card-foreground">{selection.title}</dd>
        </dl>

        {needsReason ? (
          <div className="mt-5">
            <label htmlFor={reasonId} className="text-sm font-medium text-card-foreground">
              Motivo (obligatorio)
            </label>
            <textarea
              ref={reasonRef}
              id={reasonId}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              disabled={submitting}
              required
              maxLength={DISCARD_REASON_LIMITS.max}
              aria-describedby={counterId}
              rows={3}
              className="mt-1 w-full resize-y rounded-md border bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
            />
            <p id={counterId} className="mt-1 text-xs text-muted-foreground">
              {`${Math.min(reasonLetters, DISCARD_REASON_LIMITS.min)}/${DISCARD_REASON_LIMITS.min} letras o números · ${reasonLength}/${DISCARD_REASON_LIMITS.max} caracteres`}
            </p>
            {reasonLetters < DISCARD_REASON_LIMITS.min ? (
              <p className="mt-1 text-xs text-muted-foreground">
                {`Faltan ${DISCARD_REASON_LIMITS.min - reasonLetters} letras o números; los espacios y signos no cuentan.`}
              </p>
            ) : null}
            {reasonHasHiddenCharacters(reason) ? (
              <p className="mt-1 text-xs font-medium text-destructive">
                El motivo contiene caracteres invisibles o de control; escríbelo de nuevo.
              </p>
            ) : null}
          </div>
        ) : null}

        <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            ref={cancelRef}
            type="button"
            onClick={cancel}
            disabled={submitting}
            className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-4 text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-wait disabled:opacity-60"
          >
            Cancelar
          </button>
          <button
            type="button"
            onClick={() => void confirm(reason)}
            disabled={!canConfirm}
            className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting ? copy.submitting : copy.confirm}
          </button>
        </div>
      </div>
    </div>
  );
}
