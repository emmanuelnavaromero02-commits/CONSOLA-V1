"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  bootstrapTenantAdmin,
  createUser,
  createTenant,
  createTenantWorkspace,
  deleteUser,
  deleteVaultConnection,
  deleteVaultSecret,
  cancelOperationWorkflow,
  getOperationalMetrics,
  getOperationsHealth,
  getOperationWorkflow,
  listTenants,
  listTenantWorkspaces,
  listOperationWorkflows,
  listAuditEvents,
  listUsers,
  listVaultConnections,
  listVaultSecrets,
  revealVaultConnection,
  revealVaultSecret,
  sendPasswordReset,
  triggerOperationWorkflow,
  updateUser,
  upsertVaultConnection,
  upsertVaultSecret,
} from "./client";
import type {
  AppUser,
  AuditEvent,
  BootstrapTenantAdminRequest,
  BootstrapTenantAdminResponse,
  CreateUserRequest,
  OperationalMetrics,
  OperationWorkflow,
  OperationWorkflowActionResponse,
  OperationWorkflowDetailResponse,
  OperationsHealth,
  TenantCreateRequest,
  TenantCreateResponse,
  TenantListResponse,
  UpdateUserRequest,
  VaultConnection,
  VaultConnectionPayload,
  VaultConnectionsResponse,
  VaultSecret,
  VaultSecretPayload,
  VaultSecretsResponse,
  WorkspaceCreateRequest,
  WorkspaceCreateResponse,
  WorkspaceListResponse,
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

// ── Companies / tenants ────────────────────────────────────────

export function useTenants() {
  return useQuery<TenantListResponse>({
    queryKey: ["operations", "tenants"],
    queryFn: listTenants,
    staleTime: 30_000,
  });
}

export function useTenantWorkspaces(tenantId: string | null) {
  return useQuery<WorkspaceListResponse>({
    queryKey: ["operations", "tenants", tenantId, "workspaces"],
    queryFn: () => listTenantWorkspaces(tenantId as string),
    enabled: Boolean(tenantId),
    staleTime: 30_000,
  });
}

export function useCreateTenant() {
  const qc = useQueryClient();
  return useMutation<TenantCreateResponse, Error, TenantCreateRequest>({
    mutationFn: createTenant,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["operations", "tenants"] }),
  });
}

export function useCreateTenantWorkspace() {
  const qc = useQueryClient();
  return useMutation<WorkspaceCreateResponse, Error, { tenantId: string; payload: WorkspaceCreateRequest }>({
    mutationFn: ({ tenantId, payload }) => createTenantWorkspace(tenantId, payload),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["operations", "tenants"] });
      qc.invalidateQueries({ queryKey: ["operations", "tenants", vars.tenantId, "workspaces"] });
    },
  });
}

export function useBootstrapTenantAdmin() {
  const qc = useQueryClient();
  return useMutation<BootstrapTenantAdminResponse, Error, { tenantId: string; payload: BootstrapTenantAdminRequest }>({
    mutationFn: ({ tenantId, payload }) => bootstrapTenantAdmin(tenantId, payload),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["operations", "tenants"] });
      qc.invalidateQueries({ queryKey: ["operations", "tenants", vars.tenantId, "workspaces"] });
    },
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

// ── Workflows ──────────────────────────────────────────────────────

const ACTIVE_WORKFLOW_STATUSES = new Set(["planning", "running", "waiting_approval"]);

export function useOperationWorkflows() {
  return useQuery<OperationWorkflow[]>({
    queryKey: ["operations", "workflows"],
    queryFn: listOperationWorkflows,
    staleTime: 15_000,
    refetchInterval: (q) => {
      const rows = q.state.data as OperationWorkflow[] | undefined;
      return rows?.some((row) => ACTIVE_WORKFLOW_STATUSES.has(row.status)) ? 5_000 : false;
    },
  });
}

export function useOperationWorkflow(id: string | null) {
  return useQuery<OperationWorkflowDetailResponse>({
    queryKey: ["operations", "workflow", id],
    queryFn: () => getOperationWorkflow(id as string),
    enabled: Boolean(id),
    refetchInterval: (q) => {
      const data = q.state.data as OperationWorkflowDetailResponse | undefined;
      return data && ACTIVE_WORKFLOW_STATUSES.has(data.workflow.status) ? 5_000 : false;
    },
  });
}

export function useTriggerOperationWorkflow() {
  const qc = useQueryClient();
  return useMutation<OperationWorkflowActionResponse, Error, OperationWorkflow>({
    mutationFn: triggerOperationWorkflow,
    onSuccess: (_data, workflow) => {
      qc.invalidateQueries({ queryKey: ["operations", "workflows"] });
      qc.invalidateQueries({ queryKey: ["operations", "workflow", workflow.id] });
    },
  });
}

export function useCancelOperationWorkflow() {
  const qc = useQueryClient();
  return useMutation<OperationWorkflowActionResponse, Error, string>({
    mutationFn: cancelOperationWorkflow,
    onSuccess: (_data, workflowId) => {
      qc.invalidateQueries({ queryKey: ["operations", "workflows"] });
      qc.invalidateQueries({ queryKey: ["operations", "workflow", workflowId] });
    },
  });
}

// ── Metrics ────────────────────────────────────────────────────────

export function useOperationalMetrics() {
  return useQuery<OperationalMetrics>({
    queryKey: ["operations", "metrics"],
    queryFn: getOperationalMetrics,
    staleTime: 30_000,
  });
}

export function useOperationsHealth() {
  return useQuery<OperationsHealth>({
    queryKey: ["operations", "health"],
    queryFn: getOperationsHealth,
    staleTime: 20_000,
  });
}
