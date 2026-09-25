"use client";

import { useRef, type MutableRefObject } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  cancelSupervisedAction,
  rejectSupervisedAction,
  validateSupervisedAction,
} from "@/lib/supervised-actions/client";
import { createIntentKeyRegistry, type IntentKeyRegistry } from "@/lib/supervised-actions/idempotency";
import type { ActionMutationRequest, SupervisedAction } from "@/lib/supervised-actions/types";

type MutationFn = (id: string, request: ActionMutationRequest) => Promise<SupervisedAction>;

interface ActionMutation {
  run: () => void;
  isPending: boolean;
}

export interface SupervisedActionMutations {
  validate: ActionMutation;
  reject: ActionMutation;
  cancel: ActionMutation;
  busy: boolean;
}

function useActionMutation(
  op: string,
  label: string,
  fn: MutationFn,
  currentId: string,
  registry: MutableRefObject<IntentKeyRegistry>,
  inFlightRef: MutableRefObject<boolean>,
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
      toast.success(label);
    },
    onError: async (error) => {
      await queryClient.invalidateQueries({ queryKey: ["supervised-actions"] });
      toast.error(error instanceof Error ? error.message : "No se pudo completar la acción.");
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

  const validate = useActionMutation("validate", "Acción validada.", validateSupervisedAction, currentId, registryRef, inFlightRef);
  const reject = useActionMutation("reject", "Acción rechazada.", rejectSupervisedAction, currentId, registryRef, inFlightRef);
  const cancel = useActionMutation("cancel", "Acción cancelada.", cancelSupervisedAction, currentId, registryRef, inFlightRef);

  return {
    validate,
    reject,
    cancel,
    busy: validate.isPending || reject.isPending || cancel.isPending,
  };
}
