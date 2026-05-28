/**
 * v1.44.4 Group 1 — Operations types.
 *
 * BACKEND AUDIT (2026-05-17). Endpoints actually shipped today:
 *
 *   GET    /api/admin/users
 *     → { users: [...] }
 *
 *   POST   /api/admin/users  (CSRF)
 *     body  → { email, password, name?, role? }
 *     resp  → user dict (created)
 *
 *   PATCH  /api/admin/users/{user_id}  (CSRF)
 *     body  → { name?, role?, is_active?, password? }
 *     resp  → user dict (updated)
 *
 *   DELETE /api/admin/users/{user_id}  (CSRF)
 *     resp  → { deleted: true, id: <user_id> }
 *
 *   POST   /api/admin/users/{user_id}/send-reset  (CSRF, no body)
 *     resp  → { sent: <bool> }
 *
 *   GET    /security/audit
 *     → list of audit events
 *
 *   GET    /api/vault/connections/{cartridge}
 *   GET    /api/vault/connections/{cartridge}/{conn_id}/reveal
 *   PUT    /api/vault/connections/{cartridge}/{conn_id}
 *   DELETE /api/vault/connections/{cartridge}/{conn_id}
 *
 *   GET    /api/vault/secrets/{scope}
 *     → { keys: [...] } today; the TS client normalizes to secrets[].
 *   GET    /api/vault/secrets/{scope}/{key}/reveal
 *   PUT    /api/vault/secrets/{scope}/{key}
 *   DELETE /api/vault/secrets/{scope}/{key}
 */

// ── Users ──────────────────────────────────────────────────────────

export type UserRole =
  | "admin"
  | "workspace_admin"
  | "analyst"
  | "viewer"
  | string;

export interface AppUser {
  id:                   number;
  email:                string;
  name:                 string | null;
  role:                 UserRole;
  is_active:            boolean;
  must_change_password: boolean;
  created_at:           string | null;
  last_login:           string | null;
}

export interface UsersListResponse {
  users: AppUser[];
}

export interface CreateUserRequest {
  email:    string;
  password: string;
  name?:    string;
  role?:    UserRole;
}

export interface UpdateUserRequest {
  name?:      string;
  role?:      UserRole;
  is_active?: boolean;
  password?:  string;
}

// ── Audit ──────────────────────────────────────────────────────────

export interface AuditEvent {
  id:            number | null;
  user_id:       number | null;
  user_email:    string | null;
  action:        string | null;
  resource_type: string | null;
  resource_id:   string | null;
  details:       Record<string, unknown> | null;
  ip:            string | null;
  request_id:    string | null;
  created_at:    string | null;
}

// ── Vault ──────────────────────────────────────────────────────────

export type VaultAuthMethod = "bearer_token" | "basic" | "api_key" | "none" | string;

export interface VaultConnection {
  id?:           string;
  conn_id?:      string;
  cartridge?:    string;
  kind?:         string;
  label?:        string;
  base_url?:     string | null;
  auth_method?:  VaultAuthMethod | null;
  token?:        string | null;
  created_at?:   string | null;
  rotated_at?:   string | null;
  last_used_at?: string | null;
  [key: string]: unknown;
}

export interface VaultConnectionsResponse {
  connections: VaultConnection[];
}

export interface VaultConnectionPayload {
  base_url:     string;
  auth_method:  VaultAuthMethod;
  token?:       string;
  [key: string]: unknown;
}

export interface VaultSecret {
  key?:        string;
  name?:       string;
  value?:      string | null;
  masked?:     string | null;
  created_at?: string | null;
  rotated_at?: string | null;
  [key: string]: unknown;
}

export interface VaultSecretsResponse {
  secrets: VaultSecret[];
}

export interface VaultSecretPayload {
  value: string;
}
