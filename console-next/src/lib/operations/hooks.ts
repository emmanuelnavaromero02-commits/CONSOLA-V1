"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createUser,
  deleteUser,
  listAuditEvents,
  listUsers,
  listVaultConnections,
  sendPasswordReset,
  updateUser,
} from "./client";
import type {
  AppUser,
  AuditEvent,
  CreateUserRequest,
  UpdateUserRequest,
  VaultConnectionsResponse,
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
