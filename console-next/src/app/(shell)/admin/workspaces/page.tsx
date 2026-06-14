import { WorkspacesConsole } from "@/components/admin/WorkspacesConsole";

/**
 * Configuración/Admin · Workspaces.
 *
 * Create and list workspaces under a tenant. Backend at
 * /api/admin/workspaces (global super_admin/owner/admin only).
 */
export default function WorkspacesPage() {
  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Workspaces</h1>
        <p className="text-sm text-muted-foreground">
          Provisión de workspaces dentro de un tenant.
        </p>
      </header>

      <WorkspacesConsole />
    </main>
  );
}
