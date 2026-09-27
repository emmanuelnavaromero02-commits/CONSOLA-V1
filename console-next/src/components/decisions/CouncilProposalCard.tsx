"use client";

import { Bot, CheckCircle2, RefreshCw, UserRound, XCircle } from "lucide-react";

import { formatObservedAt } from "@/lib/control-room/experience-presenter";
import type { CouncilImpact, CouncilProposal } from "@/lib/decisions/council-client";
import type { CouncilCommand } from "@/lib/decisions/use-action-council";
import { cn } from "@/lib/utils";

import { APPROVE_LABEL, DISCARD_LABEL, RENEW_LABEL } from "./CouncilDialogs";

export const STATE_LABELS: Record<CouncilProposal["state"], string> = {
  pending_approval: "Pendiente de aprobación",
  needs_other_approver: "Espera a otra persona del equipo",
  expired: "Vencida",
  no_followup: "Sin tarea de seguimiento",
  source_changed: "Datos de origen cambiaron",
  completed: "Aprobada e implementada",
};

const SEVERITY_LABELS: Record<CouncilProposal["severity"], string> = {
  critical: "Crítica",
  high: "Alta",
  medium: "Media",
  low: "Baja",
};

const SEVERITY_TONES: Record<CouncilProposal["severity"], string> = {
  critical: "border-destructive/40 bg-destructive/10 text-destructive",
  high: "border-warning/40 bg-warning/10 text-foreground",
  medium: "border-primary/30 bg-primary/5 text-foreground",
  low: "bg-muted text-muted-foreground",
};

export function originLabel(proposal: CouncilProposal): string {
  if (proposal.origin === "system") return "Sugerida por el sistema";
  return proposal.authored_by_you ? "Propuesta por ti" : "Propuesta por una persona del equipo";
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("es-MX", { maximumFractionDigits: 2 }).format(value);
}

export function impactHeadline(impact: CouncilImpact): string {
  if (impact.kind === "money" && impact.value !== undefined && impact.currency) {
    const amount = new Intl.NumberFormat("es-MX", {
      style: "currency",
      currency: impact.currency,
      maximumFractionDigits: 2,
    }).format(impact.value);
    return `Impacto estimado: ${amount}`;
  }
  if (impact.kind === "time" && impact.value !== undefined) {
    return `Impacto en tiempo: ${formatNumber(impact.value)} horas`;
  }
  return `Impacto: ${impact.label}`;
}

function formatDate(value: string | undefined): string | null {
  if (!value) return null;
  return formatObservedAt(value);
}

const BUTTON =
  "inline-flex min-h-[40px] items-center justify-center gap-1.5 rounded-md px-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60";

export function CouncilProposalCard({
  proposal,
  highlighted,
  busy,
  onCommand,
}: {
  proposal: CouncilProposal;
  highlighted: boolean;
  busy: boolean;
  onCommand: (proposal: CouncilProposal, command: CouncilCommand, opener: HTMLElement) => void;
}) {
  const titleId = `council-title-${proposal.proposal_id.slice(0, 16)}`;
  const OriginIcon = proposal.origin === "system" ? Bot : UserRound;
  const created = formatDate(proposal.created_at);
  const commitment = proposal.commitment_date ?? null;
  const showApprove = proposal.can_approve || proposal.disabled_reason !== undefined;
  const impact = proposal.impact;

  return (
    <article
      id={proposal.decision_id ? `propuesta-${proposal.decision_id}` : undefined}
      aria-labelledby={titleId}
      data-proposal-state={proposal.state}
      className={cn(
        "rounded-lg border bg-card p-5 shadow-sm",
        highlighted && "ring-2 ring-primary",
      )}
    >
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="inline-flex items-center gap-1 rounded-md border bg-background px-2 py-0.5 font-medium text-foreground">
          <OriginIcon aria-hidden className="h-3.5 w-3.5" />
          {originLabel(proposal)}
        </span>
        <span className={cn("rounded-md border px-2 py-0.5 font-medium", SEVERITY_TONES[proposal.severity])}>
          Severidad {SEVERITY_LABELS[proposal.severity].toLowerCase()}
        </span>
        <span className="rounded-md bg-muted px-2 py-0.5 text-muted-foreground">
          {STATE_LABELS[proposal.state]}
        </span>
      </div>

      <h3 id={titleId} className="mt-3 text-base font-semibold text-card-foreground">
        {proposal.title}
      </h3>
      {proposal.section_title ? (
        <p className="text-sm text-muted-foreground">{proposal.section_title}</p>
      ) : null}

      <div className="mt-3 text-sm" data-testid="council-impact">
        <p className="font-medium text-foreground">{impactHeadline(impact)}</p>
        {impact.kind !== "none" ? (
          <p className="text-xs text-muted-foreground">
            {impact.label}
            {impact.formula ? ` · Fórmula: ${impact.formula}` : null}
          </p>
        ) : null}
      </div>

      {proposal.evidence.length > 0 ? (
        <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
          {proposal.evidence.map((entry) => (
            <div key={`${entry.label}:${entry.value}`} className="flex gap-2">
              <dt className="text-muted-foreground">{entry.label}:</dt>
              <dd className="break-words text-foreground">{entry.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {proposal.narrative ? (
        <div className="mt-3 space-y-1 border-l-2 border-primary/30 pl-3 text-sm">
          <p className="text-foreground">{proposal.narrative.explanation}</p>
          <p className="text-muted-foreground">
            Recomendación: {proposal.narrative.recommendation}
          </p>
        </div>
      ) : null}

      {created || commitment ? (
        <p className="mt-3 text-xs text-muted-foreground">
          {created ? `${proposal.origin === "system" ? "Detectado" : "Propuesta creada"} el ${created}` : null}
          {created && commitment ? " · " : null}
          {commitment ? `Compromiso al ${commitment}` : null}
        </p>
      ) : null}

      {proposal.disabled_reason ? (
        <p className="mt-3 text-sm text-muted-foreground" data-testid="council-disabled-reason">
          {proposal.disabled_reason}
        </p>
      ) : null}

      {proposal.state === "completed" ? (
        <p className="mt-3 inline-flex items-center gap-1.5 text-sm text-success">
          <CheckCircle2 aria-hidden className="h-4 w-4" />
          Tarea de seguimiento interna registrada; no se modificó ningún sistema externo (ERP).
        </p>
      ) : null}

      {showApprove || proposal.can_discard || proposal.can_renew ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {showApprove ? (
            <button
              type="button"
              disabled={!proposal.can_approve || busy}
              onClick={(event) => onCommand(proposal, "approve", event.currentTarget)}
              className={cn(BUTTON, "bg-primary text-primary-foreground hover:bg-primary/90")}
            >
              <CheckCircle2 aria-hidden className="h-4 w-4" />
              {APPROVE_LABEL}
            </button>
          ) : null}
          {proposal.can_discard ? (
            <button
              type="button"
              disabled={busy}
              onClick={(event) => onCommand(proposal, "discard", event.currentTarget)}
              className={cn(BUTTON, "border border-destructive/40 bg-background text-destructive hover:bg-destructive/5")}
            >
              <XCircle aria-hidden className="h-4 w-4" />
              {DISCARD_LABEL}
            </button>
          ) : null}
          {proposal.can_renew ? (
            <button
              type="button"
              disabled={busy}
              onClick={(event) => onCommand(proposal, "renew", event.currentTarget)}
              className={cn(BUTTON, "border bg-background text-foreground hover:bg-muted")}
            >
              <RefreshCw aria-hidden className="h-4 w-4" />
              {RENEW_LABEL}
            </button>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}
