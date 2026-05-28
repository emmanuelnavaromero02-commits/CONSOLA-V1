import { AgentsConsole } from "@/components/agents/AgentsConsole";

export default function AgentsPage() {
  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Agentes</h1>
        <p className="text-sm text-muted-foreground">
          Inventario de agentes corporativos y estado de ejecución permitido.
        </p>
      </header>
      <AgentsConsole />
    </main>
  );
}
