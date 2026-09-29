"use client";

import { useRef, useState, type MutableRefObject } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  cancelSupervisedAction,
  rejectSupervisedAction,
  validateSupervisedAction,
} from "@/lib/supervised-actions/client";
import { createIntentKeyRegistry, type IntentKeyRegistry } from "@/lib/supervised-actions/idempotency";
import type { ActionMutationRequest, SupervisedAction } from "@/lib/supervised-actions/types";

type MutationFn = (id: string, request: ActionMutationRequest) => Promise<SupervisedAction>;

export interface MutationNotice {
  kind: "success" | "error";
  text: string;
}

interface ActionMutation {
  run: () => void;
  isPending: boolean;
}

export interface SupervisedActionMutations {
  validate: ActionMutation;
  reject: ActionMutation;
  cancel: ActionMutation;
  busy: boolean;
  notice: MutationNotice | null;
}

function useActionMutation(
  op: string,
  label: string,
  fn: MutationFn,
  currentId: string,
  registry: MutableRefObject<IntentKeyRegistry>,
  inFlightRef: MutableRefObject<boolean>,
  onNotice: (notice: MutationNotice) => void,
): ActionMutation {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => {
      const intent = `${op}:${currentId}`;
      return fn(currentId, { idempotency_key: registry.current.keyFor(intent) });
    },
    onSuccess: async () => {
      registry.current.complete(`${op}:${currentId}`);
      await queryClient.invalidateQueries({ queryKey: ["supervised-actions"] });
      onNotice({ kind: "success", text: label });
    },
    onError: async (error) => {
      await queryClient.invalidateQueries({ queryKey: ["supervised-actions"] });
      onNotice({
        kind: "error",
        text: error instanceof Error ? error.message : "No se pudo completar la acción.",
      });
    },
    onSettled: () => {
      inFlightRef.current = false;
    },
  });
  return {
    run: () => {
      if (inFlightRef.current || !currentId) return;
      inFlightRef.current = true;
      mutation.mutate();
    },
    isPending: mutation.isPending,
  };
}

export function useSupervisedActionMutations(currentId: string): SupervisedActionMutations {
  const registryRef = useRef(createIntentKeyRegistry());
  const inFlightRef = useRef(false);
  const [notice, setNotice] = useState<MutationNotice | null>(null);

  const validate = useActionMutation("validate", "Acción validada.", validateSupervisedAction, currentId, registryRef, inFlightRef, setNotice);
  const reject = useActionMutation("reject", "Acción rechazada.", rejectSupervisedAction, currentId, registryRef, inFlightRef, setNotice);
  const cancel = useActionMutation("cancel", "Acción cancelada.", cancelSupervisedAction, currentId, registryRef, inFlightRef, setNotice);

  return {
    validate,
    reject,
    cancel,
    busy: validate.isPending || reject.isPending || cancel.isPending,
    notice,
  };
}
