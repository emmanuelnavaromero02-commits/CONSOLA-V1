import { CreateTenantForm } from "@/components/admin/CreateTenantForm";
import { TenantsTable } from "@/components/admin/TenantsTable";

/**
 * Configuración/Admin · Tenants.
 *
 * Backend at /api/admin/tenants (global super_admin/owner/admin only,
 * console/app/main.py). Provisions tenant → workspace → first admin,
 * the chain that previously only existed as raw SQL.
 */
export default function TenantsPage() {
  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Tenants</h1>
          <p className="text-sm text-muted-foreground">
            Provisión de tenants y su workspace/admin inicial.
          </p>
        </div>
        <CreateTenantForm />
      </header>

      <TenantsTable />
    </main>
  );
}
