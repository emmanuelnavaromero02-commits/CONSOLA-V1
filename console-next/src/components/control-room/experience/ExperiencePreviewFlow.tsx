"use client";

import { useEffect, useId, useRef } from "react";

import type { ExperiencePreviewSelection } from "@/lib/control-room/use-control-room-experience-preview";

type PreviewPhase =
  | "idle"
  | "confirming"
  | "submitting"
  | "success"
  | "safe-error";

interface Props {
  phase: PreviewPhase;
  selection: ExperiencePreviewSelection | null;
  opener: HTMLElement | null;
  message: string | null;
  cancelPreview: () => void;
  confirmPreview: () => Promise<void>;
}

export function ExperiencePreviewFlow(props: Props) {
  return (
    <>
      {props.selection ? <ExperiencePreviewDialog {...props} /> : null}
      {props.phase === "success" && props.message ? (
        <div
          className="mt-5 border-l-2 border-success bg-success/10 px-4 py-3 text-sm text-foreground"
          role="status"
        >
          {props.message}
        </div>
      ) : null}
      {props.phase === "safe-error" && props.message ? (
        <div
          className="mt-5 border-l-2 border-destructive bg-destructive/10 px-4 py-3 text-sm text-foreground"
          role="alert"
        >
          {props.message}
        </div>
      ) : null}
    </>
  );
}

function ExperiencePreviewDialog({
  phase,
  selection,
  opener,
  cancelPreview,
  confirmPreview,
}: Props) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const phaseRef = useRef(phase);
  const submitting = phase === "submitting";

  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    cancelRef.current?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        if (phaseRef.current !== "submitting") cancelPreview();
        return;
      }
      if (event.key !== "Tab") return;
      if (phaseRef.current === "submitting") {
        event.preventDefault();
        dialogRef.current?.focus();
        return;
      }
      const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      ) ?? [])];
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
  }, [cancelPreview, opener]);

  useEffect(() => {
    if (submitting) dialogRef.current?.focus();
  }, [submitting]);

  if (!selection) return null;

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
          Confirmar preview
        </h2>
        <p id={descriptionId} className="mt-2 text-sm text-muted-foreground">
          Se generará una vista previa. No se realizará ningún cambio externo.
        </p>

        <dl className="mt-5 space-y-3 text-sm">
          <div>
            <dt className="font-medium text-muted-foreground">Información</dt>
            <dd className="break-words text-card-foreground">{selection.factTitle}</dd>
          </div>
          {selection.entityLabel ? (
            <div>
              <dt className="font-medium text-muted-foreground">Entidad</dt>
              <dd className="break-words text-card-foreground">
                {selection.entityLabel}
              </dd>
            </div>
          ) : null}
          <div>
            <dt className="font-medium text-muted-foreground">Acción</dt>
            <dd className="break-words text-card-foreground">
              {selection.actionLabel}
            </dd>
          </div>
        </dl>

        {selection.requiresApproval ? (
          <p className="mt-4 text-sm text-muted-foreground">
            Si continúas, una fase posterior requerirá aprobación.
          </p>
        ) : null}

        <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            ref={cancelRef}
            type="button"
            onClick={cancelPreview}
            disabled={submitting}
            className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-4 text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-wait disabled:opacity-60"
          >
            Cancelar
          </button>
          <button
            type="button"
            onClick={() => void confirmPreview()}
            disabled={submitting}
            className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-wait disabled:opacity-60"
          >
            {submitting ? "Generando preview…" : "Confirmar preview"}
          </button>
        </div>
      </div>
    </div>
  );
}
