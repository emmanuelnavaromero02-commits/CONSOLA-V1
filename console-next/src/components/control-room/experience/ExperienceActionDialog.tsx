"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";

import type { ExperienceActionKind } from "@/lib/control-room/experience-contract";
import {
  type ExperienceActionPhase,
  type ExperienceActionSelection,
  REASON_LIMITS,
  normalizeReason,
  reasonHasHiddenCharacters,
  reasonIsValid,
  reasonVisibleLength,
} from "@/lib/control-room/use-control-room-experience-action";

export const COUNCIL_LINK_LABEL = "Ver en el Consejo de Acciones";

const dialogCopy: Record<
  Exclude<ExperienceActionKind, "studio_adjustment">,
  { description: string; submitting: string }
> = {
  followup_task: {
    description: "Se generará una vista previa. No se realizará ningún cambio externo.",
    submitting: "Generando preview…",
  },
  exception_approval: {
    description:
      "El hallazgo se archivará como excepción aprobada; puedes reabrirlo.",
    submitting: "Guardando…",
  },
  decision_proposal: {
    description:
      "Se crea una decisión con compromiso a 7 días y pasa al Consejo para aprobación.",
    submitting: "Creando propuesta…",
  },
  exception_reopen: {
    description: "El hallazgo volverá a la lista de hallazgos abiertos.",
    submitting: "Reabriendo…",
  },
};

interface Props {
  phase: ExperienceActionPhase;
  selection: ExperienceActionSelection | null;
  opener: HTMLElement | null;
  message: string | null;
  href: string | null;
  cancelAction: () => void;
  confirmAction: (reason?: string) => Promise<void>;
}

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

export function ExperienceActionDialog(props: Props) {
  return (
    <>
      {props.selection ? <ActionDialog {...props} selection={props.selection} /> : null}
      {props.phase === "success" && props.message ? (
        <ResultBanner
          role="status"
          className="mt-5 border-l-2 border-success bg-success/10 px-4 py-3 text-sm text-foreground"
        >
          <p>{props.message}</p>
          {props.href ? (
            <Link
              href={props.href}
              className="mt-2 inline-flex font-medium text-primary underline-offset-4 hover:underline"
            >
              {COUNCIL_LINK_LABEL}
            </Link>
          ) : null}
        </ResultBanner>
      ) : null}
      {props.phase === "safe-error" && props.message ? (
        <ResultBanner
          role="alert"
          className="mt-5 border-l-2 border-destructive bg-destructive/10 px-4 py-3 text-sm text-foreground"
        >
          {props.message}
        </ResultBanner>
      ) : null}
    </>
  );
}

function ActionDialog({
  phase,
  selection,
  opener,
  cancelAction,
  confirmAction,
}: Props & { selection: ExperienceActionSelection }) {
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
  const isPreview = selection.kind === "followup_task";
  const copy = dialogCopy[selection.kind as keyof typeof dialogCopy];
  const limits = REASON_LIMITS[selection.kind];
  const reasonLength = Array.from(normalizeReason(reason)).length;
  const reasonLetters = reasonVisibleLength(reason);
  const canConfirm = !submitting && reasonIsValid(selection.kind, reason);

  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    (reasonRef.current ?? cancelRef.current)?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        if (phaseRef.current !== "submitting") cancelAction();
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
  }, [cancelAction, opener]);

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
          {isPreview ? "Confirmar preview" : selection.actionLabel}
        </h2>
        <p id={descriptionId} className="mt-2 text-sm text-muted-foreground">
          {copy.description}
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
            <dd className="break-words text-card-foreground">{selection.actionLabel}</dd>
          </div>
        </dl>

        {limits ? (
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
              maxLength={limits.max}
              aria-describedby={counterId}
              rows={3}
              className="mt-1 w-full resize-y rounded-md border bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
            />
            <p id={counterId} className="mt-1 text-xs text-muted-foreground">
              {`${Math.min(reasonLetters, limits.min)}/${limits.min} letras o números · ${reasonLength}/${limits.max} caracteres`}
            </p>
            {reasonLetters < limits.min ? (
              <p className="mt-1 text-xs text-muted-foreground">
                {`Faltan ${limits.min - reasonLetters} letras o números; los espacios y signos no cuentan.`}
              </p>
            ) : null}
            {reasonHasHiddenCharacters(reason) ? (
              <p className="mt-1 text-xs font-medium text-destructive">
                El motivo contiene caracteres invisibles o de control; escríbelo de nuevo.
              </p>
            ) : null}
          </div>
        ) : null}

        {isPreview && selection.requiresApproval ? (
          <p className="mt-4 text-sm text-muted-foreground">
            Si continúas, una fase posterior requerirá aprobación.
          </p>
        ) : null}

        <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            ref={cancelRef}
            type="button"
            onClick={cancelAction}
            disabled={submitting}
            className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-4 text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-wait disabled:opacity-60"
          >
            Cancelar
          </button>
          <button
            type="button"
            onClick={() => void confirmAction(reason)}
            disabled={!canConfirm}
            className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting
              ? copy.submitting
              : isPreview
                ? "Confirmar preview"
                : selection.actionLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
