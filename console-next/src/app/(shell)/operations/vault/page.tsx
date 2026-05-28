import { VaultConnectionsTable } from "@/components/operations/VaultConnectionsTable";

/**
 * v1.44.4 Group 1 — Operations · Vault.
 *
 * Inventory and controlled credential actions for Vault. All reads,
 * reveals and writes go through the same-origin FastAPI proxy and keep
 * backend permission/audit checks server-enforced.
 */
export default function VaultPage() {
  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Vault</h1>
        <p className="text-sm text-muted-foreground">
          Conexiones, API keys y secrets por cartucho/scope con revelado
          controlado y acciones auditadas.
        </p>
      </header>

      <VaultConnectionsTable />
    </main>
  );
}
