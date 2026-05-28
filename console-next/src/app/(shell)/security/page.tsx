import { AuditTable } from "@/components/operations/AuditTable";
import { SessionsTable } from "@/components/security/SessionsTable";

export default function SecurityPage() {
  return (
    <main className="mx-auto max-w-7xl space-y-8 px-6 py-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Security Center</h1>
        <p className="text-sm text-muted-foreground">
          Sesiones activas, revocación y auditoría reciente.
        </p>
      </header>

      <section className="space-y-4" aria-label="Sesiones activas">
        <div className="space-y-1">
          <h2 className="text-xl font-semibold tracking-tight">Sesiones</h2>
          <p className="text-sm text-muted-foreground">
            Revoca sesiones por identificador seguro sin exponer tokens completos.
          </p>
        </div>
        <SessionsTable />
      </section>

      <section className="space-y-4" aria-label="Auditoría">
        <div className="space-y-1">
          <h2 className="text-xl font-semibold tracking-tight">Auditoría</h2>
          <p className="text-sm text-muted-foreground">
            Últimos eventos con request-id y detalles sanitizados.
          </p>
        </div>
        <AuditTable />
      </section>
    </main>
  );
}
