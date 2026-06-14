"use client";

import { useTenants } from "@/lib/admin/tenants";

/** Read-only list of tenants with their workspace counts. */
export function TenantsTable() {
  const { data, isLoading, isError, error } = useTenants();

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Cargando tenants…</p>;
  }
  if (isError) {
    const msg = error instanceof Error ? error.message : "Error desconocido.";
    return (
      <p className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
        No se pudo cargar la lista de tenants: {msg}
      </p>
    );
  }
  const tenants = data ?? [];
  if (tenants.length === 0) {
    return <p className="text-sm text-muted-foreground">Aún no hay tenants. Crea el primero.</p>;
  }

  return (
    <div className="overflow-hidden rounded-lg border">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-4 py-2 font-medium">Nombre</th>
            <th className="px-4 py-2 font-medium">Workspaces</th>
            <th className="px-4 py-2 font-medium">Creado</th>
            <th className="px-4 py-2 font-medium">ID</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {tenants.map((t) => (
            <tr key={t.id} className="hover:bg-muted/20">
              <td className="px-4 py-2 font-medium">{t.name}</td>
              <td className="px-4 py-2 tabular-nums">{t.workspace_count}</td>
              <td className="px-4 py-2 text-muted-foreground">
                {t.created_at ? new Date(t.created_at).toLocaleString() : "—"}
              </td>
              <td className="px-4 py-2 font-mono text-[11px] text-muted-foreground">{t.id}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
