import { MeAccessPanel } from "@/components/me/MeAccessPanel";

export default function MePage() {
  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Mi perfil</h1>
        <p className="text-sm text-muted-foreground">
          Identidad, permisos efectivos y cartuchos visibles para tu sesión.
        </p>
      </header>
      <MeAccessPanel />
    </main>
  );
}
