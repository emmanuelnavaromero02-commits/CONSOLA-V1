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
 *     resp  → { sent: <bool>, temporary_password?: <string>, password_delivery?: <string> }
 *
 *   GET    /api/admin/tenants
 *   POST   /api/admin/tenants  (CSRF)
 *   GET    /api/admin/tenants/{tenant_id}/workspaces
 *   POST   /api/admin/tenants/{tenant_id}/workspaces  (CSRF)
 *   POST   /api/admin/tenants/{tenant_id}/bootstrap-admin  (CSRF)
 *   POST   /api/admin/tenants/{tenant_id}/admins/{user_id}/temporary-password  (CSRF)
 *
 *   GET    /security/audit
 *     → list of audit events
 *
 *   GET    /api/copilot/workflow
 *   GET    /api/copilot/workflow/{workflow_id}
 *   POST   /api/copilot/workflow/{workflow_id}/plan     (CSRF)
 *   POST   /api/copilot/workflow/{workflow_id}/execute  (CSRF)
 *   POST   /api/copilot/workflow/{workflow_id}/cancel   (CSRF)
 *
 *   GET    /api/metrics/operational
 *   GET    /api/operations/health
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
  tenant_id?:           string | null;
  workspaces?:          UserWorkspaceSummary[];
  created_at:           string | null;
  last_login:           string | null;
}

export interface UserWorkspaceSummary {
  workspace_id?:   string | null;
  workspace_name?: string | null;
  tenant_id?:      string | null;
  tenant_name?:    string | null;
  workspace_role?: string | null;
}

export interface UsersListResponse {
  users: AppUser[];
}

export interface SendPasswordResetResponse {
  sent:                boolean;
  temporary_password?: string | null;
  password_delivery?:  string | null;
}

export interface CreateUserRequest {
  email:        string;
  password:     string;
  name?:        string;
  role?:        UserRole;
  workspace_id: string;
}

export interface UpdateUserRequest {
  name?:      string;
  role?:      UserRole;
  is_active?: boolean;
  password?:  string;
}


export interface TenantSummary {
  id: string;
  name: string;
  slug: string;
  status: string;
  created_at: string | null;
  updated_at?: string | null;
  workspace_count: number;
  user_count: number;
}

export interface WorkspaceSummary {
  id: string;
  tenant_id: string;
  name: string;
  created_at: string | null;
  user_count: number;
  tenant_admins?: TenantAdminSummary[];
}

export interface TenantListResponse {
  tenants: TenantSummary[];
}

export interface TenantCreateRequest {
  name: string;
  slug?: string;
}

export interface TenantCreateResponse {
  tenant: TenantSummary;
  created: boolean;
}

export interface WorkspaceListResponse {
  workspaces: WorkspaceSummary[];
}

export interface WorkspaceCreateRequest {
  name: string;
}

export interface WorkspaceCreateResponse {
  workspace: WorkspaceSummary;
  created: boolean;
}

export interface TenantAdminSummary {
  id: number;
  email: string;
  name?: string | null;
  role: UserRole;
  workspace_role: "tenant_admin";
  is_active: boolean;
  must_change_password: boolean;
  tenant_id?: string | null;
  created_at: string | null;
}

export interface BootstrapTenantAdminRequest {
  workspace_id: string;
  email: string;
  name?: string;
}

export interface BootstrapTenantAdminResponse {
  user: AppUser;
  created: boolean;
  tenant_id: string;
  workspace_id: string;
  workspace_role: "tenant_admin";
  temporary_password?: string | null;
  password_delivery: "one_time_response" | "existing_user_no_password_generated";
  login_url: string;
}

export interface IssueTenantAdminTemporaryPasswordRequest {
  workspace_id: string;
}


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


export type OperationWorkflowStatus =
  | "planning"
  | "running"
  | "waiting_approval"
  | "completed"
  | "cancelled"
  | "failed"
  | string;

export type OperationWorkflowStepStatus =
  | "pending"
  | "running"
  | "waiting_approval"
  | "completed"
  | "failed"
  | "skipped"
  | string;

export interface OperationWorkflow {
  id:              string;
  intent?:         string | null;
  status:          OperationWorkflowStatus;
  current_step?:   number | null;
  error?:          string | null;
  created_at?:     string | null;
  finished_at?:    string | null;
  conversation_id?: string | null;
  plan?:           unknown;
}

export interface OperationWorkflowStep {
  id?:          number | string;
  workflow_id?: string;
  step_idx:     number;
  description?: string | null;
  tool?:        string | null;
  args?:        Record<string, unknown> | null;
  result?:      unknown;
  status:       OperationWorkflowStepStatus;
  started_at?:  string | null;
  finished_at?: string | null;
}

export interface OperationWorkflowListResponse {
  workflows: OperationWorkflow[];
}

export interface OperationWorkflowDetailResponse {
  workflow: OperationWorkflow;
  steps: OperationWorkflowStep[];
}

export interface OperationWorkflowActionResponse {
  ok?: boolean;
  workflow_id?: string;
  status?: OperationWorkflowStatus;
  step_results?: unknown[];
  error?: string | null;
}


export interface SlowEntityMetric {
  cartridge_id?: string | null;
  entity_name?: string | null;
  avg_sec?: number | string | null;
}

export interface OperationalMetrics {
  extractions_24h: number;
  errors_24h: number;
  avg_duration_seconds: number;
  slowest_entities_7d: SlowEntityMetric[];
  audit_events_24h: number;
}

export interface ServiceProbe {
  name: string;
  status: "up" | "down" | string;
  code?: number | null;
  error?: string | null;
}

export interface OperationsHealth {
  version: string;
  services: ServiceProbe[];
  summary: {
    total: number;
    up: number;
    down: number;
  };
}
