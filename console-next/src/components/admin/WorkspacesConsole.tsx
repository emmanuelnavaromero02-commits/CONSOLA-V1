"use client";

import { useEffect, useState } from "react";

import { toast } from "sonner";

import { useTenants } from "@/lib/admin/tenants";
import { useCreateWorkspace, useWorkspaces } from "@/lib/admin/workspaces";

const MAX_NAME = 100;
const MIN_NAME = 2;

/**
 * Configuración/Admin → Workspaces.
 *
 * Pick a tenant, see its workspaces, create new ones under it.
 * Backed by /api/admin/workspaces (global admin only).
 */
export function WorkspacesConsole() {
  const tenantsQuery = useTenants();
  const [tenantId, setTenantId] = useState<string>("");
  const [name, setName] = useState("");
  const createMutation = useCreateWorkspace();

  // Default the selector to the first tenant once tenants load.
  useEffect(() => {
    if (!tenantId && tenantsQuery.data && tenantsQuery.data.length > 0) {
      setTenantId(tenantsQuery.data[0].id);
    }
  }, [tenantId, tenantsQuery.data]);

  const workspacesQuery = useWorkspaces(tenantId || null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!tenantId) {
      toast.error("Selecciona un tenant.");
      return;
    }
    const trimmed = name.trim();
    if (trimmed.length < MIN_NAME) {
      toast.error(`El nombre debe tener al menos ${MIN_NAME} caracteres.`);
      return;
    }
    try {
      const res = await createMutation.mutateAsync({ tenant_id: tenantId, name: trimmed });
      toast.success(`Workspace ${res.name} creado.`);
      setName("");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Error desconocido.";
      toast.error(`No se pudo crear: ${msg}`);
    }
  }

  const tenants = tenantsQuery.data ?? [];
  const workspaces = workspacesQuery.data ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <label className="space-y-1.5 text-sm">
          <span className="font-medium">Tenant</span>
          <select
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value)}
            disabled={tenantsQuery.isLoading || tenants.length === 0}
            className="min-h-[44px] min-w-[16rem] rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {tenants.length === 0 && <option value="">Sin tenants</option>}
            {tenants.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <form
        onSubmit={handleSubmit}
        className="flex flex-wrap items-end gap-3 rounded-lg border bg-card p-5 shadow-sm"
        aria-label="Nuevo workspace"
      >
        <label className="space-y-1.5 text-sm">
          <span className="font-medium">Nuevo workspace</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            minLength={MIN_NAME}
            maxLength={MAX_NAME}
            placeholder="acme-dev"
            className="min-h-[44px] min-w-[16rem] rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </label>
        <button
          type="submit"
          disabled={createMutation.isPending || !tenantId}
          className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
        >
          {createMutation.isPending ? "Creando…" : "Crear workspace"}
        </button>
      </form>

      {workspacesQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Cargando workspaces…</p>
      ) : workspacesQuery.isError ? (
        <p className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          No se pudo cargar la lista de workspaces.
        </p>
      ) : workspaces.length === 0 ? (
        <p className="text-sm text-muted-foreground">Este tenant aún no tiene workspaces.</p>
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">Nombre</th>
                <th className="px-4 py-2 font-medium">Creado</th>
                <th className="px-4 py-2 font-medium">ID</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {workspaces.map((w) => (
                <tr key={w.id} className="hover:bg-muted/20">
                  <td className="px-4 py-2 font-medium">{w.name}</td>
                  <td className="px-4 py-2 text-muted-foreground">
                    {w.created_at ? new Date(w.created_at).toLocaleString() : "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-muted-foreground">{w.id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
