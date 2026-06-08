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
          Las contraseñas y claves de acceso de cada conector, guardadas de forma segura.
        </p>
      </header>

      <VaultConnectionsTable />
    </main>
  );
}
