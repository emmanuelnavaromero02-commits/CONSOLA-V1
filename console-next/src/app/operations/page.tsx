import Link from "next/link";

/**
 * v1.44.4 Task B — /operations placeholder.
 *
 * The proactive briefing analyser (proactive_service.py:238)
 * emits ``action_href="/operations"`` for stuck-job highlights.
 * Without this placeholder, clicking the action button on a
 * briefing card would 404 because the full Operations module is
 * Task D scope. The placeholder renders a "Próximamente" panel
 * with a clear link to the legacy operations UI on :8000 so
 * the operator can still act on the alert today.
 *
 * Replace this file in Task D with the real Operations index
 * (vault / audit / users / workspaces / settings / monitor).
 */
const LEGACY_OPERATIONS_URL =
  process.env.NEXT_PUBLIC_LEGACY_CONSOLE_URL
  ?? "http://localhost:8000/operations";


export default function OperationsPlaceholderPage() {
  return (
    <main className="mx-auto max-w-3xl space-y-6 px-6 py-10">
      <header className="space-y-2">
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Próximamente
        </p>
        <h1 className="text-3xl font-semibold tracking-tight">
          Operaciones
        </h1>
        <p className="text-sm text-muted-foreground">
          El nuevo centro de operaciones (Vault, Auditoría, Usuarios,
          Workspaces, Monitor, Configuración) llega en la siguiente
          iteración. Mientras tanto, puedes acceder a la versión
          actual desde la consola clásica.
        </p>
      </header>

      <section
        aria-label="Acceso a la consola clásica"
        className="rounded-lg border bg-card p-5 shadow-sm"
      >
        <h2 className="text-base font-semibold tracking-tight">
          Abrir en la consola clásica
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Te llevamos a la pantalla de operaciones actual. Tu sesión
          se reusa automáticamente.
        </p>
        <a
          href={LEGACY_OPERATIONS_URL}
          rel="noopener noreferrer"
          className="mt-4 inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Ir a Operaciones (clásica) →
        </a>
      </section>

      <p className="text-xs text-muted-foreground">
        ¿Buscas otra cosa?{" "}
        <Link
          href="/dashboard"
          className="font-medium text-foreground underline-offset-2 hover:underline"
        >
          Volver al panel
        </Link>
        {" · "}
        <Link
          href="/workspace"
          className="font-medium text-foreground underline-offset-2 hover:underline"
        >
          Abrir el copiloto
        </Link>
      </p>
    </main>
  );
}
