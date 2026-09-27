"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useQueryClient } from "@tanstack/react-query";

import { isApiError } from "@/lib/api";
import {
  approveControlRoomException,
  createControlRoomDecisionProposal,
  previewControlRoomExperienceAction,
  reopenControlRoomException,
  resolveControlRoomStudioTarget,
} from "@/lib/control-room/experience-client";
import type {
  ControlRoomExperienceV2,
  ExperienceAction,
  ExperienceActionKind,
} from "@/lib/control-room/experience-contract";
import { invalidateControlRoomLive } from "@/lib/control-room/use-control-room-live";

export const STALE_COPY =
  "La acción ya no está disponible. Actualiza la información e inténtalo nuevamente.";
const PREVIEW_ERROR_COPY =
  "No se pudo generar el preview. Actualiza la información e inténtalo nuevamente.";
const ACTION_ERROR_COPY =
  "No se pudo completar la acción. Actualiza la información e inténtalo nuevamente.";
const REASON_ERROR_COPY = "Revisa el motivo e inténtalo nuevamente.";

export const REASON_LIMITS: Partial<Record<ExperienceActionKind, { min: number; max: number }>> = {
  exception_approval: { min: 10, max: 500 },
  exception_reopen: { min: 3, max: 500 },
};

const INVISIBLE_OR_CONTROL = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u;
// Characters that render blank although Unicode classes them as letters/symbols.
const BLANK_FILLERS = /[\u115f\u1160\u17b4\u17b5\u2800\u3164\uffa0]/u;
const LETTER_OR_DIGIT = /[\p{L}\p{N}]/gu;

export function normalizeReason(value: string): string {
  return value.replace(/[\t\n\r ]+/g, " ").trim();
}

export function reasonHasHiddenCharacters(value: string): boolean {
  const spaced = value.replace(/[\t\n\r]/g, " ");
  return INVISIBLE_OR_CONTROL.test(spaced) || BLANK_FILLERS.test(spaced);
}

export function reasonVisibleLength(value: string): number {
  return normalizeReason(value).match(LETTER_OR_DIGIT)?.length ?? 0;
}

export function reasonIsValid(kind: ExperienceActionKind, value: string): boolean {
  const limits = REASON_LIMITS[kind];
  if (!limits) return true;
  if (reasonHasHiddenCharacters(value)) return false;
  const length = Array.from(normalizeReason(value)).length;
  return reasonVisibleLength(value) >= limits.min && length <= limits.max;
}

export interface ExperienceActionSubject {
  title: string;
  entity_label?: string;
}

export interface ExperienceActionSelection {
  kind: ExperienceActionKind;
  factTitle: string;
  entityLabel?: string;
  actionLabel: string;
  requiresApproval: boolean;
}

interface BoundSelection extends ExperienceActionSelection {
  actionHandle: string;
  workspaceId: string | null;
  opener: HTMLElement;
  idempotencyKey: string;
}

type ActionState =
  | { phase: "idle" }
  | { phase: "confirming"; selection: BoundSelection }
  | { phase: "submitting"; selection: BoundSelection }
  | { phase: "success"; message: string; href: string | null }
  | { phase: "safe-error"; message: string };

export type ExperienceActionPhase = ActionState["phase"];

export type OpenExperienceAction = (
  subject: ExperienceActionSubject,
  action: ExperienceAction,
  opener: HTMLElement,
) => void;

function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `cr-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

function actionsOf(experience: ControlRoomExperienceV2) {
  return [
    ...experience.sections.flatMap((section) =>
      section.facts.flatMap((fact) =>
        fact.actions.map((action) => ({ subject: fact as ExperienceActionSubject, action })),
      ),
    ),
    ...(experience.exceptions ?? []).flatMap((exception) =>
      exception.actions.map((action) => ({
        subject: exception as ExperienceActionSubject,
        action,
      })),
    ),
  ];
}

function selectionIsCurrent(
  experience: ControlRoomExperienceV2 | undefined,
  workspaceId: string | null,
  selection: BoundSelection,
): boolean {
  if (!experience) return false;
  if (workspaceId !== selection.workspaceId) return false;
  const matches = actionsOf(experience).filter(
    ({ action }) => action.action_handle === selection.actionHandle,
  );
  if (matches.length !== 1) return false;
  const { subject, action } = matches[0];
  return (
    action.enabled &&
    action.kind === selection.kind &&
    subject.title === selection.factTitle &&
    subject.entity_label === selection.entityLabel &&
    action.label === selection.actionLabel &&
    action.requires_approval === selection.requiresApproval
  );
}

async function submit(
  selection: BoundSelection,
  reason: string,
): Promise<{ message: string; href: string | null }> {
  switch (selection.kind) {
    case "followup_task": {
      const result = await previewControlRoomExperienceAction(selection.actionHandle);
      return { message: result.message, href: null };
    }
    case "exception_approval": {
      const result = await approveControlRoomException(
        selection.actionHandle,
        normalizeReason(reason),
        selection.idempotencyKey,
      );
      return { message: result.message, href: null };
    }
    case "exception_reopen": {
      const result = await reopenControlRoomException(
        selection.actionHandle,
        normalizeReason(reason),
        selection.idempotencyKey,
      );
      return { message: result.message, href: null };
    }
    case "decision_proposal": {
      const result = await createControlRoomDecisionProposal(
        selection.actionHandle,
        selection.idempotencyKey,
      );
      return { message: result.message, href: result.href };
    }
    case "studio_adjustment": {
      const result = await resolveControlRoomStudioTarget(selection.actionHandle);
      return { message: "", href: result.href };
    }
  }
}

export function useControlRoomExperienceAction(
  experience: ControlRoomExperienceV2 | undefined,
  workspaceId: string | null,
  navigate: (href: string) => void = () => undefined,
) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<ActionState>({ phase: "idle" });
  const stateRef = useRef(state);
  const submittingRef = useRef(false);
  const attemptRef = useRef(0);
  const experienceRef = useRef(experience);
  const workspaceRef = useRef(workspaceId);
  const navigateRef = useRef(navigate);

  useLayoutEffect(() => {
    experienceRef.current = experience;
    workspaceRef.current = workspaceId;
    navigateRef.current = navigate;
  }, [experience, workspaceId, navigate]);

  const transition = useCallback((next: ActionState) => {
    stateRef.current = next;
    setState(next);
  }, []);

  const refreshActiveExperience = useCallback(
    (activeWorkspace: string | null) => {
      void invalidateControlRoomLive(queryClient, activeWorkspace).catch(
        () => undefined,
      );
    },
    [queryClient],
  );

  const failStale = useCallback(
    (activeWorkspace: string | null) => {
      submittingRef.current = false;
      transition({ phase: "safe-error", message: STALE_COPY });
      refreshActiveExperience(activeWorkspace);
    },
    [refreshActiveExperience, transition],
  );

  const run = useCallback(
    async (selection: BoundSelection, reason: string) => {
      submittingRef.current = true;
      const attempt = ++attemptRef.current;
      transition({ phase: "submitting", selection });
      try {
        const result = await submit(selection, reason);
        if (
          attemptRef.current !== attempt ||
          workspaceRef.current !== selection.workspaceId
        ) {
          return;
        }
        if (selection.kind === "studio_adjustment" && result.href) {
          transition({ phase: "idle" });
          navigateRef.current(result.href);
          return;
        }
        transition({ phase: "success", message: result.message, href: result.href });
        refreshActiveExperience(selection.workspaceId);
      } catch (error) {
        if (
          attemptRef.current !== attempt ||
          workspaceRef.current !== selection.workspaceId
        ) {
          return;
        }
        if (isApiError(error) && (error.status === 404 || error.status === 409)) {
          failStale(selection.workspaceId);
          return;
        }
        const message =
          isApiError(error) && error.status === 422 && REASON_LIMITS[selection.kind]
            ? REASON_ERROR_COPY
            : selection.kind === "followup_task"
              ? PREVIEW_ERROR_COPY
              : ACTION_ERROR_COPY;
        transition({ phase: "safe-error", message });
      } finally {
        submittingRef.current = false;
      }
    },
    [failStale, refreshActiveExperience, transition],
  );

  const openAction = useCallback<OpenExperienceAction>(
    (subject, action, opener) => {
      if (!action.enabled || submittingRef.current) return;
      const selection: BoundSelection = {
        kind: action.kind,
        actionHandle: action.action_handle,
        workspaceId,
        factTitle: subject.title,
        entityLabel: subject.entity_label,
        actionLabel: action.label,
        requiresApproval: action.requires_approval,
        opener,
        idempotencyKey: newIdempotencyKey(),
      };
      if (action.kind === "studio_adjustment") {
        void run(selection, "");
        return;
      }
      transition({ phase: "confirming", selection });
    },
    [run, transition, workspaceId],
  );

  const cancelAction = useCallback(() => {
    if (submittingRef.current) return;
    transition({ phase: "idle" });
  }, [transition]);

  const confirmAction = useCallback(
    async (reason = "") => {
      if (submittingRef.current) return;
      const current = stateRef.current;
      if (current.phase !== "confirming") return;
      const { selection } = current;
      if (!reasonIsValid(selection.kind, reason)) return;
      if (
        !selectionIsCurrent(experienceRef.current, workspaceRef.current, selection)
      ) {
        failStale(workspaceRef.current);
        return;
      }
      await run(selection, reason);
    },
    [failStale, run],
  );

  useEffect(() => {
    const current = stateRef.current;
    if (current.phase === "confirming") {
      if (!selectionIsCurrent(experience, workspaceId, current.selection)) {
        failStale(workspaceId);
      }
      return;
    }
    if (
      current.phase === "submitting" &&
      workspaceId !== current.selection.workspaceId
    ) {
      attemptRef.current += 1;
      submittingRef.current = false;
      transition({ phase: "safe-error", message: STALE_COPY });
      refreshActiveExperience(workspaceId);
    }
  }, [experience, failStale, refreshActiveExperience, transition, workspaceId]);

  const activeSelection =
    state.phase === "confirming" || state.phase === "submitting"
      ? state.selection
      : null;
  const dialogSelection =
    activeSelection && activeSelection.kind !== "studio_adjustment"
      ? activeSelection
      : null;

  return {
    phase: state.phase,
    selection: dialogSelection
      ? {
          kind: dialogSelection.kind,
          factTitle: dialogSelection.factTitle,
          entityLabel: dialogSelection.entityLabel,
          actionLabel: dialogSelection.actionLabel,
          requiresApproval: dialogSelection.requiresApproval,
        }
      : null,
    opener: dialogSelection?.opener ?? null,
    message:
      state.phase === "success" || state.phase === "safe-error"
        ? state.message
        : null,
    href: state.phase === "success" ? state.href : null,
    dialogOpen: dialogSelection !== null,
    openAction,
    cancelAction,
    confirmAction,
  } as const;
}
