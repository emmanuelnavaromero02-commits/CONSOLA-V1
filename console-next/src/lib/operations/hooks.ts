"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createUser,
  deleteUser,
  deleteVaultConnection,
  deleteVaultSecret,
  listAuditEvents,
  listUsers,
  listVaultConnections,
  listVaultSecrets,
  revealVaultConnection,
  revealVaultSecret,
  sendPasswordReset,
  updateUser,
  upsertVaultConnection,
  upsertVaultSecret,
} from "./client";
import type {
  AppUser,
  AuditEvent,
  CreateUserRequest,
  UpdateUserRequest,
  VaultConnection,
  VaultConnectionPayload,
  VaultConnectionsResponse,
  VaultSecret,
  VaultSecretPayload,
  VaultSecretsResponse,
} from "./types";

// ── Users ──────────────────────────────────────────────────────────

export function useUsers() {
  return useQuery<AppUser[]>({
    queryKey: ["operations", "users"],
    queryFn:  listUsers,
    staleTime: 30_000,
  });
}

export function useCreateUser() {
  const qc = useQueryClient();
  return useMutation<AppUser, Error, CreateUserRequest>({
    mutationFn: createUser,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["operations", "users"] }),
  });
}

export function useUpdateUser() {
  const qc = useQueryClient();
  return useMutation<AppUser, Error, { userId: number; patch: UpdateUserRequest }>({
    mutationFn: ({ userId, patch }) => updateUser(userId, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["operations", "users"] }),
  });
}

export function useDeleteUser() {
  const qc = useQueryClient();
  return useMutation<void, Error, number>({
    mutationFn: deleteUser,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["operations", "users"] }),
  });
}

export function useSendPasswordReset() {
  return useMutation<void, Error, number>({
    mutationFn: sendPasswordReset,
  });
}

// ── Audit ──────────────────────────────────────────────────────────

export function useAuditEvents() {
  return useQuery<AuditEvent[]>({
    queryKey: ["operations", "audit"],
    queryFn:  listAuditEvents,
    staleTime: 60_000,
  });
}

// ── Vault ──────────────────────────────────────────────────────────

export function useVaultConnections(cartridge: string | null) {
  return useQuery<VaultConnectionsResponse>({
    queryKey: ["operations", "vault", cartridge],
    queryFn:  () => listVaultConnections(cartridge as string),
    enabled:  Boolean(cartridge),
    staleTime: 30_000,
  });
}

export function useRevealVaultConnection() {
  return useMutation<VaultConnection, Error, { cartridge: string; connId: string }>({
    mutationFn: ({ cartridge, connId }) => revealVaultConnection(cartridge, connId),
  });
}

export function useUpsertVaultConnection() {
  const qc = useQueryClient();
  return useMutation<VaultConnection, Error, { cartridge: string; connId: string; payload: VaultConnectionPayload }>({
    mutationFn: ({ cartridge, connId, payload }) => upsertVaultConnection(cartridge, connId, payload),
    onSuccess: (_data, vars) => qc.invalidateQueries({ queryKey: ["operations", "vault", vars.cartridge] }),
  });
}

export function useDeleteVaultConnection() {
  const qc = useQueryClient();
  return useMutation<void, Error, { cartridge: string; connId: string }>({
    mutationFn: ({ cartridge, connId }) => deleteVaultConnection(cartridge, connId),
    onSuccess: (_data, vars) => qc.invalidateQueries({ queryKey: ["operations", "vault", vars.cartridge] }),
  });
}

export function useVaultSecrets(scope: string | null) {
  return useQuery<VaultSecretsResponse>({
    queryKey: ["operations", "vault", "secrets", scope],
    queryFn:  () => listVaultSecrets(scope as string),
    enabled:  Boolean(scope),
    staleTime: 30_000,
  });
}

export function useRevealVaultSecret() {
  return useMutation<VaultSecret, Error, { scope: string; key: string }>({
    mutationFn: ({ scope, key }) => revealVaultSecret(scope, key),
  });
}

export function useUpsertVaultSecret() {
  const qc = useQueryClient();
  return useMutation<VaultSecret, Error, { scope: string; key: string; payload: VaultSecretPayload }>({
    mutationFn: ({ scope, key, payload }) => upsertVaultSecret(scope, key, payload),
    onSuccess: (_data, vars) => qc.invalidateQueries({ queryKey: ["operations", "vault", "secrets", vars.scope] }),
  });
}

export function useDeleteVaultSecret() {
  const qc = useQueryClient();
  return useMutation<void, Error, { scope: string; key: string }>({
    mutationFn: ({ scope, key }) => deleteVaultSecret(scope, key),
    onSuccess: (_data, vars) => qc.invalidateQueries({ queryKey: ["operations", "vault", "secrets", vars.scope] }),
  });
}
