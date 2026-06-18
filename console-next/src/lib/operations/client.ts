import { api, isApiError } from "@/lib/api";
import type {
  AppUser,
  AuditEvent,
  BootstrapTenantAdminRequest,
  BootstrapTenantAdminResponse,
  CreateUserRequest,
  IssueTenantAdminTemporaryPasswordRequest,
  OperationalMetrics,
  OperationWorkflow,
  OperationWorkflowActionResponse,
  OperationWorkflowDetailResponse,
  OperationWorkflowListResponse,
  OperationsHealth,
  TenantCreateRequest,
  TenantCreateResponse,
  TenantListResponse,
  SendPasswordResetResponse,
  UpdateUserRequest,
  UsersListResponse,
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

export async function listUsers(): Promise<AppUser[]> {
  const { data } = await api.get<UsersListResponse>("/api/admin/users");
  return data.users ?? [];
}

export async function createUser(req: CreateUserRequest): Promise<AppUser> {
  const { data } = await api.post<AppUser>("/api/admin/users", req);
  return data;
}

export async function updateUser(
  userId: number,
  req: UpdateUserRequest,
): Promise<AppUser> {
  const { data } = await api.patch<AppUser>(
    `/api/admin/users/${userId}`,
    req,
  );
  return data;
}

export async function deleteUser(userId: number): Promise<void> {
  await api.delete(`/api/admin/users/${userId}`);
}

export async function sendPasswordReset(userId: number): Promise<SendPasswordResetResponse> {
  const { data } = await api.post<SendPasswordResetResponse>(
    `/api/admin/users/${userId}/send-reset`,
    {},
  );
  return data;
}

// ── Companies / tenants ────────────────────────────────────────

export async function listTenants(): Promise<TenantListResponse> {
  const { data } = await api.get<TenantListResponse>("/api/admin/tenants");
  return { tenants: data.tenants ?? [] };
}

export async function createTenant(req: TenantCreateRequest): Promise<TenantCreateResponse> {
  const { data } = await api.post<TenantCreateResponse>("/api/admin/tenants", req);
  return data;
}

export async function listTenantWorkspaces(tenantId: string): Promise<WorkspaceListResponse> {
  const { data } = await api.get<WorkspaceListResponse>(
    `/api/admin/tenants/${encodeURIComponent(tenantId)}/workspaces`,
  );
  return { workspaces: data.workspaces ?? [] };
}

export async function createTenantWorkspace(
  tenantId: string,
  req: WorkspaceCreateRequest,
): Promise<WorkspaceCreateResponse> {
  const { data } = await api.post<WorkspaceCreateResponse>(
    `/api/admin/tenants/${encodeURIComponent(tenantId)}/workspaces`,
    req,
  );
  return data;
}

export async function bootstrapTenantAdmin(
  tenantId: string,
  req: BootstrapTenantAdminRequest,
): Promise<BootstrapTenantAdminResponse> {
  const { data } = await api.post<BootstrapTenantAdminResponse>(
    `/api/admin/tenants/${encodeURIComponent(tenantId)}/bootstrap-admin`,
    req,
  );
  return data;
}

export async function issueTenantAdminTemporaryPassword(
  tenantId: string,
  userId: number,
  req: IssueTenantAdminTemporaryPasswordRequest,
): Promise<BootstrapTenantAdminResponse> {
  const { data } = await api.post<BootstrapTenantAdminResponse>(
    `/api/admin/tenants/${encodeURIComponent(tenantId)}/admins/${encodeURIComponent(userId)}/temporary-password`,
    req,
  );
  return data;
}

// ── Audit ──────────────────────────────────────────────────────────

export async function listAuditEvents(): Promise<AuditEvent[]> {
  const { data } = await api.get<AuditEvent[] | { events: AuditEvent[] }>(
    "/security/audit",
  );
  if (Array.isArray(data)) return data;
  return data.events ?? [];
}

// ── Vault ──────────────────────────────────────────────────────────

export async function listVaultConnections(
  cartridge: string,
): Promise<VaultConnectionsResponse> {
  const { data } = await api.get<VaultConnectionsResponse>(
    `/api/vault/connections/${encodeURIComponent(cartridge)}`,
  );
  return { connections: data.connections ?? [] };
}

export async function revealVaultConnection(
  cartridge: string,
  connId: string,
): Promise<VaultConnection> {
  const { data } = await api.get<VaultConnection>(
    `/api/vault/connections/${encodeURIComponent(cartridge)}/${encodeURIComponent(connId)}/reveal`,
  );
  return data;
}

export async function upsertVaultConnection(
  cartridge: string,
  connId: string,
  payload: VaultConnectionPayload,
): Promise<VaultConnection> {
  const { data } = await api.put<VaultConnection>(
    `/api/vault/connections/${encodeURIComponent(cartridge)}/${encodeURIComponent(connId)}`,
    payload,
  );
  return data;
}

export async function deleteVaultConnection(
  cartridge: string,
  connId: string,
): Promise<void> {
  await api.delete(
    `/api/vault/connections/${encodeURIComponent(cartridge)}/${encodeURIComponent(connId)}`,
  );
}

export async function listVaultSecrets(scope: string): Promise<VaultSecretsResponse> {
  const { data } = await api.get<VaultSecretsResponse | { keys?: string[] }>(
    `/api/vault/secrets/${encodeURIComponent(scope)}`,
  );
  if ("secrets" in data && Array.isArray(data.secrets)) {
    return { secrets: data.secrets };
  }
  const keys = "keys" in data && Array.isArray(data.keys) ? data.keys : [];
  return { secrets: keys.map((key) => ({ key, masked: "••••••••••" })) };
}

export async function revealVaultSecret(scope: string, key: string): Promise<VaultSecret> {
  const { data } = await api.get<VaultSecret>(
    `/api/vault/secrets/${encodeURIComponent(scope)}/${encodeURIComponent(key)}/reveal`,
  );
  return data;
}

export async function upsertVaultSecret(
  scope: string,
  key: string,
  payload: VaultSecretPayload,
): Promise<VaultSecret> {
  const { data } = await api.put<VaultSecret>(
    `/api/vault/secrets/${encodeURIComponent(scope)}/${encodeURIComponent(key)}`,
    payload,
  );
  return data;
}

export async function deleteVaultSecret(scope: string, key: string): Promise<void> {
  await api.delete(`/api/vault/secrets/${encodeURIComponent(scope)}/${encodeURIComponent(key)}`);
}

// ── Workflows ──────────────────────────────────────────────────────

export async function listOperationWorkflows(): Promise<OperationWorkflow[]> {
  const { data } = await api.get<OperationWorkflowListResponse>("/api/copilot/workflow");
  return data.workflows ?? [];
}

export async function getOperationWorkflow(id: string): Promise<OperationWorkflowDetailResponse> {
  const { data } = await api.get<OperationWorkflowDetailResponse>(
    `/api/copilot/workflow/${encodeURIComponent(id)}`,
  );
  return {
    workflow: data.workflow,
    steps: data.steps ?? [],
  };
}

export async function triggerOperationWorkflow(workflow: OperationWorkflow): Promise<OperationWorkflowActionResponse> {
  const id = encodeURIComponent(workflow.id);
  if (workflow.status === "planning") {
    try {
      await api.post(`/api/copilot/workflow/${id}/plan`, {});
    } catch (error) {
      if (!(isApiError(error) && error.status === 409)) {
        throw error;
      }
    }
  }
  const { data } = await api.post<OperationWorkflowActionResponse>(
    `/api/copilot/workflow/${id}/execute`,
    {},
  );
  return data;
}

export async function cancelOperationWorkflow(workflowId: string): Promise<OperationWorkflowActionResponse> {
  const { data } = await api.post<OperationWorkflowActionResponse>(
    `/api/copilot/workflow/${encodeURIComponent(workflowId)}/cancel`,
    {},
  );
  return data;
}

// ── Metrics ────────────────────────────────────────────────────────

export async function getOperationalMetrics(): Promise<OperationalMetrics> {
  const { data } = await api.get<OperationalMetrics>("/api/metrics/operational");
  return {
    extractions_24h: Number(data.extractions_24h ?? 0),
    errors_24h: Number(data.errors_24h ?? 0),
    avg_duration_seconds: Number(data.avg_duration_seconds ?? 0),
    slowest_entities_7d: data.slowest_entities_7d ?? [],
    audit_events_24h: Number(data.audit_events_24h ?? 0),
  };
}

export async function getOperationsHealth(): Promise<OperationsHealth> {
  const { data } = await api.get<OperationsHealth>("/api/operations/health");
  return {
    version: data.version ?? "unknown",
    services: data.services ?? [],
    summary: data.summary ?? { total: 0, up: 0, down: 0 },
  };
}
