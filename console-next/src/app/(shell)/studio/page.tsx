"use client";

import { Info } from "lucide-react";

import { useKpis } from "@/lib/hooks/useKpis";
import { legacyConsoleUrl } from "@/lib/legacy-url";

import { CartridgeLauncherCard } from "@/components/studio/CartridgeLauncherCard";

/**
 * v1.44.4 Group 1 — slim Studio launcher.
 *
 * BACKEND REALITY (audited 2026-05-17):
 *   - Every /api/studio/* endpoint is a v1.44.3.3 stub returning
 *     ``{stub: true, version: "v1.44.3.3", …empty…}``.
 *   - The legacy /studio HTML at :8000 still works end-to-end —
 *     it calls /studio/* (no /api prefix) + /api/mcp/invoke for
 *     real Deploy / Materialise / etc.
 *   - Only real-data signal the Next.js side has is the
 *     per-cartridge freshness from /api/dashboard/kpis.
 *
 * Per the scope decision (option B in the v1.44.4 Group 1
 * brief), this page is a SLIM LAUNCHER, not a full UI:
 *
 *   - 4 cartridge cards showing real freshness + status.
 *   - Each card links to /studio?cartridge=<id> on the legacy
 *     :8000 origin via the LEGACY_CONSOLE_URL env var.
 *   - A clear banner at the top explains the migration in
 *     progress so an operator isn't surprised by the bounce
 *     to the classic UI.
 *
 * Replace this page with the full Next.js Studio in v1.44.5
 * once the /api/studio/* endpoints graduate from stubs.
 */
const CARTRIDGES: { id: string; name: string; description: string }[] = [
  {
    id:          "replicon",
    name:        "Replicon",
    description: "Time tracking + project hours.",
  },
  {
    id:          "sap_hcm",
    name:        "SAP HCM",
    description: "Recursos humanos. Empleados, puestos, organización.",
  },
  {
    id:          "sap_s4hana",
    name:        "SAP S/4HANA",
    description: "Financiero + logística. Cuentas, asientos, materiales.",
  },
  {
    id:          "sap_successfactors",
    name:        "SAP SuccessFactors",
    description: "Talento + performance. Goals, reviews, learning.",
  },
];


export default function StudioPage() {
  const { data, isLoading, isError, refetch } = useKpis();
  const freshnessMap = data?.data_freshness ?? {};

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Studio</h1>
        <p className="text-sm text-muted-foreground">
          Configura DAGs, refinamiento de capas y semántica para cada cartucho.
        </p>
      </header>

      <aside
        role="note"
        className="flex flex-col gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 text-sm sm:flex-row sm:items-start"
      >
        <span
          aria-hidden
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400"
        >
          <Info className="h-4 w-4" />
        </span>
        <div className="space-y-1">
          <p className="font-medium">Studio se está migrando a Next.js.</p>
          <p className="text-xs text-muted-foreground">
            Mientras tanto, abre cada cartucho en la consola clásica
            (puerto 8000). El estado de frescura que ves abajo viene
            de los KPIs del panel —no es una estimación.
          </p>
        </div>
      </aside>

      {isError ? (
        <div
          role="alert"
          aria-live="polite"
          className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm"
        >
          <p className="font-medium text-destructive">
            No se pudo cargar el estado de los cartuchos.
          </p>
          <button
            type="button"
            onClick={() => refetch()}
            className="mt-2 inline-flex min-h-[44px] items-center justify-center rounded-md border border-destructive/40 px-3 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40"
          >
            Reintentar
          </button>
        </div>
      ) : null}

      <section
        aria-label="Cartuchos disponibles"
        className="grid grid-cols-1 gap-4 sm:grid-cols-2"
      >
        {isLoading && !data ? (
          Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className="h-44 animate-pulse rounded-lg border bg-card"
              aria-hidden
            />
          ))
        ) : (
          CARTRIDGES.map((c) => (
            <CartridgeLauncherCard
              key={c.id}
              id={c.id}
              name={c.name}
              description={c.description}
              freshness={freshnessMap[c.id]}
              legacyHref={legacyConsoleUrl(
                `/studio?cartridge=${encodeURIComponent(c.id)}`,
              )}
            />
          ))
        )}
      </section>
    </main>
  );
}
