"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

import { AppCatalog } from "@/components/analytics/AppCatalog";

function AnalyticsShell() {
  const params = useSearchParams();
  return (
    <main className="mx-auto max-w-[1600px] space-y-6 px-6 py-8">
      <header className="space-y-1">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Analítica
        </p>
        <h1 className="text-2xl font-semibold tracking-tight">Aplicaciones analíticas</h1>
        <p className="text-sm text-muted-foreground">
          Dashboards publicados por los cartuchos activos. Para lo que requiere tu
          atención ahora, usa el Control Room.
        </p>
      </header>
      <AppCatalog cartridge={params.get("cartridge") ?? undefined} />
    </main>
  );
}

export default function AnalyticsPage() {
  return (
    <Suspense
      fallback={<div className="p-6 text-sm text-muted-foreground">Cargando catálogo…</div>}
    >
      <AnalyticsShell />
    </Suspense>
  );
}
