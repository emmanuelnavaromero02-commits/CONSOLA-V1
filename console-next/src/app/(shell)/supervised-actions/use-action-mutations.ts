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
      // La clave de la intención se conserva dentro de este montaje: un
      // reintento manual tras un timeout o error ambiguo envía exactamente
      // la misma idempotency_key. Además se re-sincroniza el estado del
      // servidor para resolver de forma visible si la mutación llegó a
      // aplicarse antes de que el usuario decida reintentar.
      await queryClient.invalidateQueries({ queryKey: ["supervised-actions"] });
      toast.error(error instanceof Error ? error.message : "No se pudo completar la acción.");
    },
    onSettled: () => {
      inFlightRef.current = false;
    },
  });
  return {
    run: () => {
      // Guard síncrono contra doble clic: solo una solicitud activa.
      if (inFlightRef.current || !currentId) return;
      inFlightRef.current = true;
      mutation.mutate();
    },
    isPending: mutation.isPending,
  };
}

/**
 * Mutaciones de preparación (validar/rechazar/cancelar) con idempotencia
 * por intención lógica (operación + id de acción), acotada honestamente
 * al montaje actual:
 * - dentro del montaje, un reintento tras timeout/error ambiguo reutiliza
 *   exactamente la misma clave y esta solo rota tras éxito definitivo;
 * - tras un desenlace ambiguo se re-sincroniza el estado del servidor
 *   antes de que el usuario pueda reintentar;
 * - NO se promete idempotencia entre montajes, pestañas o recargas.
 *
 * DEPENDENCIA CONTRACTUAL EXPLÍCITA (PR-B): el contrato actual expone el
 * id durable de la acción pero ninguna identidad/versión de intención
 * server-authoritative. Sin ese campo no existe forma legítima de derivar
 * una clave estable que sobreviva un remount sin heurísticas ni storage
 * sensible (prohibidos). Cuando PR-B entregue esa identidad, este módulo
 * es el único punto a reconectar.
 */
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
