/**
 * v1.44.4 Group 1 — Operations API client.
 *
 * Thin typed wrappers against the REAL backend endpoints documented
 * in ./types.ts. The shared ``api`` client from @/lib/api handles
 * CSRF, cookies and request IDs on same-origin FastAPI calls.
 *
 * NOTE on the /security/audit path: that router is mounted at
 * the bare /security prefix (NOT /api/security), so it is intentionally
 * requested as a relative FastAPI URL.
 */
import { api } from "@/lib/api";
import type {
  AppUser,
  AuditEvent,
  CreateUserRequest,
  UpdateUserRequest,
  UsersListResponse,
  VaultConnection,
  VaultConnectionPayload,
  VaultConnectionsResponse,
  VaultSecret,
  VaultSecretPayload,
  VaultSecretsResponse,
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
  // Round 1 Backend P0: real backend is PATCH, not PUT
  // (console/app/main.py:3543). Previous draft used api.put
  // which 405'd every edit.
  const { data } = await api.patch<AppUser>(
    `/api/admin/users/${userId}`,
    req,
  );
  return data;
}


export async function deleteUser(userId: number): Promise<void> {
  await api.delete(`/api/admin/users/${userId}`);
}


export async function sendPasswordReset(userId: number): Promise<void> {
  await api.post(`/api/admin/users/${userId}/send-reset`, {});
}


// ── Audit ──────────────────────────────────────────────────────────


/**
 * GET /security/audit returns an ARRAY (not an envelope) — the
 * router does ``return res`` where ``res`` is a list of dicts.
 * Keep this normalised so callers always see ``AuditEvent[]``.
 */
export async function listAuditEvents(): Promise<AuditEvent[]> {
  const { data } = await api.get<AuditEvent[] | { events: AuditEvent[] }>(
    "/security/audit",
  );
  if (Array.isArray(data)) return data;
  // Defensive — if a future refactor wraps the response,
  // unwrap rather than crash.
  return (data as { events?: AuditEvent[] }).events ?? [];
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
  await api.delete(`/api/vault/connections/${encodeURIComponent(cartridge)}/${encodeURIComponent(connId)}`);
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
