import { DecisionsBoard } from "@/components/decisions/DecisionsBoard";

export default function DecisionsPage() {
  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Decisiones</h1>
        <p className="text-sm text-muted-foreground">
          Registro operativo de compromisos, seguimiento y cierre.
        </p>
      </header>
      <DecisionsBoard />
    </main>
  );
}
