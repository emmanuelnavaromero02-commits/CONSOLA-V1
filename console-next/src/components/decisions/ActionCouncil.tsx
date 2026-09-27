"use client";

import { Inbox, Loader2, RefreshCcw } from "lucide-react";
import { useEffect, useRef } from "react";

import { EmptyState } from "@/components/EmptyState";
import { ExperienceLiveBadge } from "@/components/control-room/experience/ExperienceLiveBadge";
import { useControlRoomLive } from "@/lib/control-room/use-control-room-live";
import { useActionCouncil, useCouncilActions } from "@/lib/decisions/use-action-council";
import { cn } from "@/lib/utils";

import { CouncilDialog, CouncilResult } from "./CouncilDialogs";
import { CouncilProposalCard } from "./CouncilProposalCard";

export const COUNCIL_EMPTY_TITLE = "No hay propuestas pendientes";
export const COUNCIL_EMPTY_DESCRIPTION =
  "Cuando el sistema detecte hallazgos accionables o alguien cree una propuesta de decisión desde el Control Room, aparecerán aquí.";
export const COUNCIL_LOAD_ERROR = "No se pudo cargar el Consejo de Acciones.";
export const COUNCIL_FOCUS_MISSING =
  "La propuesta indicada ya no está pendiente o no es visible para ti.";

export function ActionCouncil({ focusDecisionId }: { focusDecisionId: number | null }) {
  const query = useActionCouncil();
  const actions = useCouncilActions(query.data, query.workspaceId);
  const live = useControlRoomLive({
    workspaceId: query.workspaceId,
    experience: query,
    paused: actions.dialogOpen,
  });
  const focused = useRef<number | null>(null);
  const proposals = query.data?.proposals ?? [];
  const focusFound =
    focusDecisionId !== null &&
    proposals.some((proposal) => proposal.decision_id === focusDecisionId);

  useEffect(() => {
    if (focusDecisionId === null || !focusFound || focused.current === focusDecisionId) return;
    focused.current = focusDecisionId;
    document
      .getElementById(`propuesta-${focusDecisionId}`)
      ?.scrollIntoView?.({ block: "center" });
  }, [focusDecisionId, focusFound]);

  return (
    <section aria-label="Consejo de Acciones Sugeridas" className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm text-muted-foreground">
            Propuestas sugeridas por el sistema o creadas por el equipo. Cada una necesita la
            aprobación de una persona distinta de quien la propuso.
          </p>
          <ExperienceLiveBadge
            checkedAt={live.checkedAt}
            offline={live.offline}
            sourceObservedAt={null}
          />
        </div>
        <button
          type="button"
          aria-label="Actualizar propuestas"
          onClick={live.refreshAll}
          disabled={query.isFetching}
          className="inline-flex min-h-10 min-w-10 items-center justify-center rounded-md border bg-card text-foreground shadow-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-wait disabled:opacity-60"
        >
          <RefreshCcw aria-hidden className={cn("h-4 w-4", query.isFetching && "animate-spin")} />
        </button>
      </div>

      <CouncilResult phase={actions.phase} message={actions.message} />

      {query.isPending ? (
        <div
          role="status"
          aria-busy="true"
          className="flex min-h-[200px] items-center justify-center gap-2 text-sm text-muted-foreground"
        >
          <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
          Cargando propuestas…
        </div>
      ) : query.isError && !query.data ? (
        <div
          role="alert"
          className="flex flex-col gap-3 rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive sm:flex-row sm:items-center sm:justify-between"
        >
          <span>{COUNCIL_LOAD_ERROR}</span>
          <button
            type="button"
            onClick={live.refreshAll}
            className="inline-flex min-h-[40px] items-center justify-center rounded-md border bg-background px-3 text-sm font-medium text-foreground hover:bg-muted"
          >
            Reintentar
          </button>
        </div>
      ) : proposals.length === 0 ? (
        <div className="rounded-lg border bg-card">
          <EmptyState
            icon={Inbox}
            title={COUNCIL_EMPTY_TITLE}
            description={COUNCIL_EMPTY_DESCRIPTION}
            primaryAction={{ label: "Ir al Control Room", href: "/control-room" }}
          />
        </div>
      ) : (
        <div className="space-y-3">
          {focusDecisionId !== null && !focusFound ? (
            <p className="text-sm text-muted-foreground">{COUNCIL_FOCUS_MISSING}</p>
          ) : null}
          {proposals.map((proposal) => (
            <CouncilProposalCard
              key={proposal.proposal_id}
              proposal={proposal}
              highlighted={focusDecisionId !== null && proposal.decision_id === focusDecisionId}
              busy={actions.dialogOpen}
              onCommand={actions.open}
            />
          ))}
        </div>
      )}

      {actions.selection ? (
        <CouncilDialog
          phase={actions.phase}
          selection={actions.selection}
          cancel={actions.cancel}
          confirm={actions.confirm}
        />
      ) : null}
    </section>
  );
}
