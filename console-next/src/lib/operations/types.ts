/**
 * v1.44.4 Group 1 — Operations types.
 *
 * BACKEND AUDIT (2026-05-17). Endpoints actually shipped today
 * (others mentioned in the brief don't exist and are NOT
 * surfaced in the UI):
 *
 *   GET    /api/admin/users
 *     → { users: [{ id, email, name, role, is_active,
 *                   must_change_password, created_at,
 *                   last_login }] }
 *     Permission: iam.users.read
 *
 *   POST   /api/admin/users  (CSRF)
 *     body  → { email, password, name?, role? }
 *     resp  → user dict (created)
 *     Permission: iam.users.write
 *
 *   PATCH  /api/admin/users/{user_id}  (CSRF)   [NOT PUT — real verb]
 *     body  → { name?, role?, is_active?, password? }
 *     resp  → user dict (updated)
 *     Permission: iam.users.write
 *
 *   DELETE /api/admin/users/{user_id}  (CSRF)
 *     resp  → { deleted: true, id: <user_id> }
 *     Permission: iam.users.write
 *
 *   POST   /api/admin/users/{user_id}/send-reset  (CSRF, no body)
 *     resp  → { sent: <bool> }
 *     Permission: iam.users.write
 *
 *   GET    /security/audit
 *     → list of { id, user_id, user_email, action,
 *                 resource_type, resource_id, details, ip,
 *                 created_at }
 *     Permission: security.audit.read
 *
 *   GET    /api/vault/connections/{cartridge}
 *     → { connections: [...] }
 *     Permission: vault.connections.read
 *
 *   GET    /api/vault/secrets/{scope}
 *     → { secrets: [...] }  (server emits Vault's native
 *       shape — secrets are masked unless reveal is called)
 *     Permission: vault.secrets.read_masked
 */

// ── Users ──────────────────────────────────────────────────────────


export type UserRole =
  | "admin"
  | "workspace_admin"
  | "analyst"
  | "viewer"
  | string; // tolerate roles the seed adds later


export interface AppUser {
  id:                    number;
  email:                 string;
  name:                  string | null;
  role:                  UserRole;
  is_active:             boolean;
  must_change_password:  boolean;
  created_at:            string | null;
  last_login:            string | null;
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
  name?:       string;
  role?:       UserRole;
  is_active?:  boolean;
  /** Server forces ``must_change_password=true`` whenever
   *  ``password`` is set on an admin update. */
  password?:   string;
}


// ── Audit ──────────────────────────────────────────────────────────


export interface AuditEvent {
  id:             number | null;
  user_id:        number | null;
  user_email:     string | null;
  action:         string | null;
  resource_type:  string | null;
  resource_id:    string | null;
  details:        Record<string, unknown> | null;
  ip:             string | null;
  created_at:     string | null;
}


// ── Vault ──────────────────────────────────────────────────────────


/**
 * Vault returns connection records keyed by cartridge. The
 * shape is determined by the upstream vault service (proxied
 * verbatim by console/app/main.py:api_vault_list_connections).
 * Common fields observed:
 *   id, cartridge, kind, label, created_at, rotated_at,
 *   last_used_at.
 * Kept loose with an index signature so the UI doesn't break
 * if Vault adds a field.
 */
export interface VaultConnection {
  id:              string;
  cartridge?:      string;
  kind?:           string;
  label?:          string;
  created_at?:     string | null;
  rotated_at?:     string | null;
  last_used_at?:   string | null;
  [key: string]:   unknown;
}


export interface VaultConnectionsResponse {
  connections: VaultConnection[];
}
