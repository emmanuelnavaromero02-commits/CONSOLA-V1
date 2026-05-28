import { ObjectExplorer } from "@/components/explorer/ObjectExplorer";

export default function ExplorerPage() {
  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Explorer</h1>
        <p className="text-sm text-muted-foreground">
          Navegación de buckets y objetos con descarga y borrado auditado.
        </p>
      </header>
      <ObjectExplorer />
    </main>
  );
}
