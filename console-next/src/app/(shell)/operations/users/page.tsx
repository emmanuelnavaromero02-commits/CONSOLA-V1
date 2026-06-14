import { CreateUserForm } from "@/components/operations/CreateUserForm";
import { UsersTable } from "@/components/operations/UsersTable";

/**
 * v1.44.4 Group 1 — Operations · Usuarios.
 *
 * Real backend at /api/admin/users (read + write require
 * ``iam.users.read`` / ``iam.users.write`` per
 * console/app/main.py:3503-3650). The middleware delegates RBAC
 * to the FastAPI dependency, so a non-authorized user reaching
 * this page sees the table's "could not load" error rather
 * than blank content.
 */
export default function UsersPage() {
  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Usuarios</h1>
          <p className="text-sm text-muted-foreground">
            Administración de cuentas dentro del workspace activo.
          </p>
        </div>
        <CreateUserForm />
      </header>

      <UsersTable />
    </main>
  );
}
