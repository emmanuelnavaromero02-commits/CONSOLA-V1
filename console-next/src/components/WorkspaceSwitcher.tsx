"use client";

import { Building2 } from "lucide-react";

import { writeCookie } from "@/lib/cookies";
import { ACTIVE_WORKSPACE_COOKIE, type WorkspaceAccessItem } from "@/lib/workspace-context";
import { cn } from "@/lib/utils";

interface WorkspaceSwitcherProps {
  workspaces?: WorkspaceAccessItem[];
  compact?: boolean;
  className?: string;
}

function labelForWorkspace(workspace: WorkspaceAccessItem): string {
  const workspaceName = workspace.workspace_name?.trim() || workspace.workspace_id || "Workspace";
  const tenantName = workspace.tenant_name?.trim();
  return tenantName ? `${tenantName} / ${workspaceName}` : workspaceName;
}

function activeWorkspaceId(workspaces: WorkspaceAccessItem[]): string {
  const active = workspaces.find((workspace) => workspace.active && workspace.workspace_id);
  return active?.workspace_id || workspaces[0]?.workspace_id || "";
}

export function WorkspaceSwitcher({
  workspaces,
  compact = false,
  className,
}: WorkspaceSwitcherProps) {
  const options = (workspaces ?? []).filter((workspace) => workspace.workspace_id);
  if (options.length <= 1) return null;
  const selected = activeWorkspaceId(options);

  function switchWorkspace(workspaceId: string) {
    if (!workspaceId || workspaceId === selected || typeof document === "undefined") return;
    writeCookie(ACTIVE_WORKSPACE_COOKIE, workspaceId, 31_536_000);
    window.location.reload();
  }

  if (compact) {
    return (
      <div className={cn("flex justify-center", className)}>
        <Building2 aria-hidden className="h-4 w-4 text-muted-foreground" />
      </div>
    );
  }

  return (
    <label className={cn("block space-y-1 text-sm", className)}>
      <span className="text-xs font-medium uppercase text-muted-foreground">Workspace activo</span>
      <div className="relative">
        <Building2 aria-hidden className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
        <select
          value={selected}
          onChange={(event) => switchWorkspace(event.target.value)}
          className="min-h-[44px] w-full rounded-md border bg-background pl-9 pr-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label="Cambiar workspace activo"
        >
          {options.map((workspace) => (
            <option key={workspace.workspace_id || labelForWorkspace(workspace)} value={workspace.workspace_id || ""}>
              {labelForWorkspace(workspace)}
            </option>
          ))}
        </select>
      </div>
    </label>
  );
}
