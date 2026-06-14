"use client";

import { useMemo } from "react";

import type { MeAccessResponse } from "@/lib/admin-surfaces";
import { ACTIVE_WORKSPACE_COOKIE } from "@/lib/workspace-context";

interface WorkspaceSwitcherProps {
  access: MeAccessResponse | undefined;
  showLabels?: boolean;
}

/**
 * Shell workspace switcher. Persists the choice in a cookie that the API
 * client echoes as the `X-Workspace-Id` header on every request, then
 * reloads so all data refetches under the new workspace. A global admin
 * sees every tenant's workspaces (grouped by tenant); a tenant_admin sees
 * their tenant's; everyone else sees their explicit memberships.
 */
export function WorkspaceSwitcher({ access, showLabels = true }: WorkspaceSwitcherProps) {
  const workspaces = access?.workspaces ?? [];
  const activeId = access?.workspace?.workspace_id ?? "";

  // Group by tenant so a super_admin can tell workspaces of different
  // tenants apart. Map preserves insertion order (already tenant-sorted).
  const byTenant = useMemo(() => {
    const groups = new Map<string, { tenantName: string; items: typeof workspaces }>();
    for (const w of workspaces) {
      const key = w.tenant_id ?? "—";
      if (!groups.has(key)) {
        groups.set(key, { tenantName: w.tenant_name ?? "—", items: [] });
      }
      groups.get(key)!.items.push(w);
    }
    return Array.from(groups.values());
  }, [workspaces]);

  // Nothing to switch between — don't clutter the shell.
  if (workspaces.length <= 1 || !showLabels) return null;

  function handleChange(event: React.ChangeEvent<HTMLSelectElement>) {
    const id = event.target.value;
    if (!id || id === activeId) return;
    // Session cookie, lax, root path — same-origin so the backend sees it
    // and the API client can read it to set X-Workspace-Id.
    document.cookie = `${ACTIVE_WORKSPACE_COOKIE}=${encodeURIComponent(id)}; path=/; SameSite=Lax`;
    window.location.reload();
  }

  return (
    <label className="block space-y-1 text-sm">
      <span className="text-xs font-medium uppercase text-muted-foreground">Workspace</span>
      <select
        value={activeId}
        onChange={handleChange}
        aria-label="Cambiar workspace"
        className="min-h-[40px] w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {byTenant.map((group) => (
          <optgroup key={group.tenantName} label={group.tenantName}>
            {group.items.map((w) => (
              <option key={w.workspace_id} value={w.workspace_id ?? ""}>
                {w.workspace_name ?? w.workspace_id}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </label>
  );
}
