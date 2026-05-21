/**
 * v1.44.4 Group 1 — Operations API client.
 *
 * Thin axios wrappers against the REAL backend endpoints
 * documented in ./types.ts. The shared ``api`` instance from
 * @/lib/api handles CSRF + cookies + same-origin proxy.
 *
 * NOTE on the /security/audit path: that router is mounted at
 * the bare /security prefix (NOT /api/security). The same-origin
 * proxy at console-next/src/app/api/[...path]/route.ts only
 * catches /api/* paths, so calls to /security/audit need to go
 * through a different proxy route. For Task D scope we route
 * them through axios directly — the global same-origin proxy
 * at console-next/src/proxy.ts won't redirect /security/*
 * because it's an authenticated path with a session cookie.
 */
import { api } from "@/lib/api";
import type {
  AppUser,
  AuditEvent,
  CreateUserRequest,
  UpdateUserRequest,
  UsersListResponse,
  VaultConnectionsResponse,
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
