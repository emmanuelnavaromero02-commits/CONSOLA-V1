import { AuditTable } from "@/components/operations/AuditTable";

/**
 * v1.44.4 Group 1 — Operations · Auditoría.
 *
 * Reads /security/audit (real backend, last 100 events). Server
 * enforces ``security.audit.read``; non-authorized users see
 * the table's error state.
 */
export default function AuditPage() {
  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Auditoría</h1>
        <p className="text-sm text-muted-foreground">
          Últimas 100 acciones registradas en el sistema.
        </p>
      </header>

      <AuditTable />
    </main>
  );
}
