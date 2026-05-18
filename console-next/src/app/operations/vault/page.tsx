import { VaultConnectionsTable } from "@/components/operations/VaultConnectionsTable";

/**
 * v1.44.4 Group 1 — Operations · Vault.
 *
 * Read-only inventory of connections per cartridge against
 * /api/vault/connections/{cartridge}. Requires
 * ``vault.connections.read`` (server-enforced); the table
 * surfaces the backend's permission error if a non-admin
 * reaches this page directly.
 */
export default function VaultPage() {
  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Vault</h1>
        <p className="text-sm text-muted-foreground">
          Conexiones almacenadas por cartucho. Las credenciales viven
          en el servicio de Vault — esta vista solo muestra metadatos.
        </p>
      </header>

      <VaultConnectionsTable />
    </main>
  );
}
