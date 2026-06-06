"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  activateCartridge,
  deleteCredentials,
  getConnectorSchema,
  listCartridges,
  saveCredentials,
  testConnection,
  type ConnectorSchema,
} from "@/lib/cartridges";

/**
 * v1.44.3 — TanStack-Query hooks for the cartridges surface.
 *
 * Read hooks are cache-shared (the grid and the detail page invalidate
 * each other via the same ``cartridges`` root key) so a save/delete in
 * the detail page refreshes the grid badge without a manual refetch.
 */

const ROOT_KEY = "cartridges";

export function useCartridgeList() {
  return useQuery({
    queryKey: [ROOT_KEY, "list"],
    queryFn: listCartridges,
    staleTime: 60_000,
  });
}

export function useConnectorSchema(cartridgeId: string | undefined) {
  return useQuery<ConnectorSchema>({
    queryKey: [ROOT_KEY, "schema", cartridgeId],
    queryFn: () => getConnectorSchema(cartridgeId as string),
    enabled: Boolean(cartridgeId),
    // The schema is static-per-cartridge so we can be aggressive here.
    staleTime: 5 * 60_000,
  });
}

export function useSaveCredentials(cartridgeId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: Record<string, string | number | boolean>) =>
      saveCredentials(cartridgeId, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [ROOT_KEY] });
    },
  });
}

export function useTestConnection(cartridgeId: string) {
  return useMutation({
    mutationFn: (connId?: string) => testConnection(cartridgeId, connId),
  });
}

export function useDeleteCredentials(cartridgeId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => deleteCredentials(cartridgeId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [ROOT_KEY] });
    },
  });
}

export function useActivateCartridge() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (cartridgeId: string) => activateCartridge(cartridgeId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [ROOT_KEY] });
      qc.invalidateQueries({ queryKey: ["dashboard", "kpis"] });
    },
  });
}
