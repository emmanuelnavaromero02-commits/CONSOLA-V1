"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { isApiError, readCookie } from "@/lib/api";
import { invalidateControlRoomLive } from "@/lib/control-room/use-control-room-live";
import {
  normalizeReason,
  reasonHasHiddenCharacters,
  reasonVisibleLength,
} from "@/lib/control-room/use-control-room-experience-action";
import {
  approveCouncilProposal,
  discardCouncilProposal,
  getActionCouncil,
  renewCouncilProposal,
  type ActionCouncil,
  type CouncilProposal,
} from "@/lib/decisions/council-client";
import { ACTIVE_WORKSPACE_COOKIE } from "@/lib/workspace-context";

export type CouncilCommand = "approve" | "discard" | "renew";

export const DISCARD_REASON_LIMITS = { min: 10, max: 500 } as const;
export const COUNCIL_STALE_COPY =
  "La propuesta cambió o ya no está disponible. Revisa el Consejo actualizado.";
export const COUNCIL_SOURCE_CHANGED_COPY =
  "Los datos de origen cambiaron; la propuesta ya no puede aprobarse tal como está.";
export const COUNCIL_FORBIDDEN_COPY =
  "Tu rol no permite esta acción; requiere a otra persona del equipo.";
export const COUNCIL_ERROR_COPY =
  "No se pudo completar la acción. Inténtalo nuevamente en unos minutos.";
export const COUNCIL_REASON_ERROR_COPY = "Revisa el motivo e inténtalo nuevamente.";
export const COUNCIL_RENEW_ERROR_COPY =
  "No se pudo renovar la propuesta; revisa el hallazgo en el Control Room.";

export const actionCouncilKey = (workspaceId: string | null) =>
  ["decisions", "council", workspaceId ?? "unscoped"] as const;

export function actionCouncilQueryOptions(
  workspaceId: string | null,
  fetcher: () => Promise<ActionCouncil> = getActionCouncil,
) {
  return {
    queryKey: actionCouncilKey(workspaceId),
    queryFn: () => fetcher(),
    retry: false,
    refetchOnMount: true,
    refetchOnReconnect: false,
    refetchOnWindowFocus: true,
    refetchInterval: false,
    staleTime: 15_000,
  } as const;
}

export function useActionCouncil() {
  const workspaceId = readCookie(ACTIVE_WORKSPACE_COOKIE);
  const query = useQuery(actionCouncilQueryOptions(workspaceId));
  return { ...query, workspaceId };
}

export function discardReasonIsValid(value: string): boolean {
  if (reasonHasHiddenCharacters(value)) return false;
  const length = Array.from(normalizeReason(value)).length;
  return (
    reasonVisibleLength(value) >= DISCARD_REASON_LIMITS.min &&
    length <= DISCARD_REASON_LIMITS.max
  );
}

export interface CouncilSelection {
  command: CouncilCommand;
  proposalId: string;
  title: string;
  workspaceId: string | null;
  opener: HTMLElement;
  idempotencyKey: string;
}

type CouncilState =
  | { phase: "idle" }
  | { phase: "confirming"; selection: CouncilSelection }
  | { phase: "submitting"; selection: CouncilSelection }
  | { phase: "success"; message: string }
  | { phase: "safe-error"; message: string };

export type CouncilPhase = CouncilState["phase"];

function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `council-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function allowed(proposal: CouncilProposal, command: CouncilCommand): boolean {
  if (command === "approve") return proposal.can_approve;
  if (command === "discard") return proposal.can_discard;
  return proposal.can_renew;
}

function stillOffered(
  council: ActionCouncil | undefined,
  workspaceId: string | null,
  selection: CouncilSelection,
): boolean {
  if (!council || workspaceId !== selection.workspaceId) return false;
  const proposal = council.proposals.find(
    (candidate) => candidate.proposal_id === selection.proposalId,
  );
  return proposal !== undefined && allowed(proposal, selection.command);
}

async function submit(selection: CouncilSelection, reason: string): Promise<string> {
  if (selection.command === "approve") {
    return (await approveCouncilProposal(selection.proposalId, selection.idempotencyKey)).message;
  }
  if (selection.command === "discard") {
    const result = await discardCouncilProposal(
      selection.proposalId,
      normalizeReason(reason),
      selection.idempotencyKey,
    );
    return result.message;
  }
  return (await renewCouncilProposal(selection.proposalId)).message;
}

function failureCopy(error: unknown, command: CouncilCommand): string {
  if (!isApiError(error)) return COUNCIL_ERROR_COPY;
  if (error.status === 403) return COUNCIL_FORBIDDEN_COPY;
  if (error.status === 422 && command === "discard") return COUNCIL_REASON_ERROR_COPY;
  if (error.status === 409 && command === "renew") return COUNCIL_RENEW_ERROR_COPY;
  const detail = (error.data as { detail?: { code?: string } } | undefined)?.detail;
  if (error.status === 409 && detail?.code === "proposal_source_changed") {
    return COUNCIL_SOURCE_CHANGED_COPY;
  }
  if (error.status === 404 || error.status === 409) return COUNCIL_STALE_COPY;
  return COUNCIL_ERROR_COPY;
}

export function useCouncilActions(
  council: ActionCouncil | undefined,
  workspaceId: string | null,
) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<CouncilState>({ phase: "idle" });
  const stateRef = useRef(state);
  const submittingRef = useRef(false);
  const attemptRef = useRef(0);
  const councilRef = useRef(council);
  const workspaceRef = useRef(workspaceId);

  useLayoutEffect(() => {
    councilRef.current = council;
    workspaceRef.current = workspaceId;
  }, [council, workspaceId]);

  const transition = useCallback((next: CouncilState) => {
    stateRef.current = next;
    setState(next);
  }, []);

  const refresh = useCallback(
    (activeWorkspace: string | null) => {
      void Promise.all([
        queryClient.invalidateQueries({
          queryKey: actionCouncilKey(activeWorkspace),
          exact: true,
          refetchType: "active",
        }),
        queryClient.invalidateQueries({ queryKey: ["decisions"], refetchType: "active" }),
        invalidateControlRoomLive(queryClient, activeWorkspace),
      ]).catch(() => undefined);
    },
    [queryClient],
  );

  const failStale = useCallback(
    (activeWorkspace: string | null) => {
      submittingRef.current = false;
      transition({ phase: "safe-error", message: COUNCIL_STALE_COPY });
      refresh(activeWorkspace);
    },
    [refresh, transition],
  );

  const open = useCallback(
    (proposal: CouncilProposal, command: CouncilCommand, opener: HTMLElement) => {
      if (submittingRef.current || !allowed(proposal, command)) return;
      transition({
        phase: "confirming",
        selection: {
          command,
          proposalId: proposal.proposal_id,
          title: proposal.title,
          workspaceId,
          opener,
          idempotencyKey: newIdempotencyKey(),
        },
      });
    },
    [transition, workspaceId],
  );

  const cancel = useCallback(() => {
    if (submittingRef.current) return;
    transition({ phase: "idle" });
  }, [transition]);

  const confirm = useCallback(
    async (reason = "") => {
      if (submittingRef.current) return;
      const current = stateRef.current;
      if (current.phase !== "confirming") return;
      const { selection } = current;
      if (selection.command === "discard" && !discardReasonIsValid(reason)) return;
      if (!stillOffered(councilRef.current, workspaceRef.current, selection)) {
        failStale(workspaceRef.current);
        return;
      }
      submittingRef.current = true;
      const attempt = ++attemptRef.current;
      transition({ phase: "submitting", selection });
      try {
        const message = await submit(selection, reason);
        if (attemptRef.current !== attempt || workspaceRef.current !== selection.workspaceId) {
          return;
        }
        transition({ phase: "success", message });
        refresh(selection.workspaceId);
      } catch (error) {
        if (attemptRef.current !== attempt || workspaceRef.current !== selection.workspaceId) {
          return;
        }
        transition({ phase: "safe-error", message: failureCopy(error, selection.command) });
        refresh(selection.workspaceId);
      } finally {
        submittingRef.current = false;
      }
    },
    [failStale, refresh, transition],
  );

  useEffect(() => {
    const current = stateRef.current;
    if (current.phase === "confirming" && !stillOffered(council, workspaceId, current.selection)) {
      failStale(workspaceId);
      return;
    }
    if (current.phase === "submitting" && workspaceId !== current.selection.workspaceId) {
      attemptRef.current += 1;
      submittingRef.current = false;
      transition({ phase: "safe-error", message: COUNCIL_STALE_COPY });
    }
  }, [council, failStale, transition, workspaceId]);

  const selection =
    state.phase === "confirming" || state.phase === "submitting" ? state.selection : null;

  return {
    phase: state.phase,
    selection,
    message: state.phase === "success" || state.phase === "safe-error" ? state.message : null,
    dialogOpen: selection !== null,
    open,
    cancel,
    confirm,
  } as const;
}
