export const ACTIVE_WORKSPACE_COOKIE = "omega_active_workspace_id";

export interface WorkspaceAccessItem {
  workspace_id?: string | null;
  workspace_name?: string | null;
  tenant_id?: string | null;
  tenant_name?: string | null;
  workspace_role?: string | null;
  active?: boolean | null;
}
