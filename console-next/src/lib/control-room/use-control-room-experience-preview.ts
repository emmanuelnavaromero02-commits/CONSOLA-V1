"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { isApiError } from "@/lib/api";
import { previewControlRoomExperienceAction } from "@/lib/control-room/experience-client";
import type {
  ControlRoomExperienceV2,
  ExperienceAction,
  ExperienceFactV2,
} from "@/lib/control-room/experience-contract";
import { controlRoomExperienceKey } from "@/lib/control-room/use-control-room-experience";

const STALE_COPY =
  "La acción ya no está disponible. Actualiza la información e inténtalo nuevamente.";
const SAFE_ERROR_COPY =
  "No se pudo generar el preview. Actualiza la información e inténtalo nuevamente.";

export interface ExperiencePreviewSelection {
  factTitle: string;
  entityLabel?: string;
  actionLabel: string;
  requiresApproval: boolean;
}

interface BoundSelection extends ExperiencePreviewSelection {
  actionHandle: string;
  workspaceId: string | null;
  opener: HTMLElement;
}

type PreviewState =
  | { kind: "idle" }
  | { kind: "confirming"; selection: BoundSelection }
  | { kind: "submitting"; selection: BoundSelection }
  | { kind: "success"; message: string }
  | { kind: "safe-error"; message: string };

export type OpenExperiencePreview = (
  fact: ExperienceFactV2,
  action: ExperienceAction,
  opener: HTMLElement,
) => void;

function selectionIsCurrent(
  experience: ControlRoomExperienceV2 | undefined,
  workspaceId: string | null,
  selection: BoundSelection,
): boolean {
  if (!experience) return false;
  if (workspaceId !== selection.workspaceId) return false;
  const matches = experience.sections.flatMap((section) =>
    section.facts.flatMap((fact) =>
      fact.actions
        .filter((action) => action.action_handle === selection.actionHandle)
        .map((action) => ({ fact, action })),
    ),
  );
  if (matches.length !== 1) return false;
  const { fact, action } = matches[0];
  return (
    action.enabled &&
    fact.title === selection.factTitle &&
    fact.entity_label === selection.entityLabel &&
    action.label === selection.actionLabel &&
    action.requires_approval === selection.requiresApproval
  );
}

export function useControlRoomExperiencePreview(
  experience: ControlRoomExperienceV2 | undefined,
  workspaceId: string | null,
) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<PreviewState>({ kind: "idle" });
  const stateRef = useRef(state);
  const submittingRef = useRef(false);
  const experienceRef = useRef(experience);
  const workspaceRef = useRef(workspaceId);

  useEffect(() => {
    experienceRef.current = experience;
    workspaceRef.current = workspaceId;
  }, [experience, workspaceId]);

  const transition = useCallback((next: PreviewState) => {
    stateRef.current = next;
    setState(next);
  }, []);

  const refreshActiveExperience = useCallback(
    (activeWorkspace: string | null) => {
      void queryClient
        .invalidateQueries({
          queryKey: controlRoomExperienceKey(activeWorkspace),
          exact: true,
          refetchType: "active",
        })
        .catch(() => undefined);
    },
    [queryClient],
  );

  const failStale = useCallback(
    (activeWorkspace: string | null) => {
      submittingRef.current = false;
      transition({ kind: "safe-error", message: STALE_COPY });
      refreshActiveExperience(activeWorkspace);
    },
    [refreshActiveExperience, transition],
  );

  const openPreview = useCallback<OpenExperiencePreview>(
    (fact, action, opener) => {
      if (!action.enabled || submittingRef.current) return;
      transition({
        kind: "confirming",
        selection: {
          actionHandle: action.action_handle,
          workspaceId,
          factTitle: fact.title,
          entityLabel: fact.entity_label,
          actionLabel: action.label,
          requiresApproval: action.requires_approval,
          opener,
        },
      });
    },
    [transition, workspaceId],
  );

  const cancelPreview = useCallback(() => {
    if (submittingRef.current) return;
    transition({ kind: "idle" });
  }, [transition]);

  const confirmPreview = useCallback(async () => {
    if (submittingRef.current) return;
    const current = stateRef.current;
    if (current.kind !== "confirming") return;
    const { selection } = current;
    if (
      !selectionIsCurrent(
        experienceRef.current,
        workspaceRef.current,
        selection,
      )
    ) {
      failStale(workspaceRef.current);
      return;
    }

    submittingRef.current = true;
    transition({ kind: "submitting", selection });
    try {
      const result = await previewControlRoomExperienceAction(selection.actionHandle);
      submittingRef.current = false;
      transition({ kind: "success", message: result.message });
      refreshActiveExperience(selection.workspaceId);
    } catch (error) {
      submittingRef.current = false;
      if (isApiError(error) && (error.status === 404 || error.status === 409)) {
        failStale(selection.workspaceId);
        return;
      }
      transition({ kind: "safe-error", message: SAFE_ERROR_COPY });
    }
  }, [failStale, refreshActiveExperience, transition]);

  useEffect(() => {
    const current = stateRef.current;
    if (
      current.kind === "confirming" &&
      !selectionIsCurrent(experience, workspaceId, current.selection)
    ) {
      failStale(workspaceId);
    }
  }, [experience, failStale, workspaceId]);

  const activeSelection =
    state.kind === "confirming" || state.kind === "submitting"
      ? state.selection
      : null;

  return {
    phase: state.kind,
    selection: activeSelection
      ? {
          factTitle: activeSelection.factTitle,
          entityLabel: activeSelection.entityLabel,
          actionLabel: activeSelection.actionLabel,
          requiresApproval: activeSelection.requiresApproval,
        }
      : null,
    opener: activeSelection?.opener ?? null,
    message:
      state.kind === "success" || state.kind === "safe-error"
        ? state.message
        : null,
    openPreview,
    cancelPreview,
    confirmPreview,
  } as const;
}
