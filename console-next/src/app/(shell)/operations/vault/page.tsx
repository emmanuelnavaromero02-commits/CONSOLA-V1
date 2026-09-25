import { VaultConnectionsTable } from "@/components/operations/VaultConnectionsTable";

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
