"use client";

import { useKpis } from "@/lib/hooks/useKpis";
import { useCartridgeList } from "@/lib/hooks/useCartridges";
import { CartridgeCard } from "@/components/cartridges/CartridgeCard";
import type { ConnectionStatus } from "@/components/cartridges/StatusBadge";

const META: Record<
  string,
  { name: string; description: string }
> = {
  replicon: {
    name: "Replicon",
    description: "Time tracking + project hours. Empleados, proyectos, time entries.",
  },
  sap_hcm: {
    name: "SAP HCM",
    description: "Recursos humanos. Empleados, puestos, organización.",
  },
  sap_s4hana: {
    name: "SAP S/4HANA",
    description: "Financiero + logística. Cuentas, asientos, materiales.",
  },
  sap_successfactors: {
    name: "SAP SuccessFactors",
    description: "Talento + performance. Goals, reviews, learning.",
  },
};

/**
 * /cartridges grid.
 *
 * The page derives ConnectionStatus from the dashboard KPI payload
 * (data_freshness) so it doesn't need a second endpoint per
 * cartridge. The mapping is:
 *   fresh / stale  → connected   (recent successful run)
 *   very_stale     → failed      (last run too old)
 *   never          → unconfigured (no successful run on record)
 *
 * "untested" — credentials saved but not yet probed — needs a
 * separate vault-read endpoint that v1.44.3 doesn't yet expose;
 * we conservatively report "untested" on the detail page after a
 * save action but never mark a grid tile that way for now.
 */
export default function CartridgesPage() {
  const list = useCartridgeList();
  const kpis = useKpis();

  const statusFor = (id: string): ConnectionStatus => {
    const info = kpis.data?.data_freshness?.[id];
    if (!info) return "unconfigured";
    if (info.status === "never") return "unconfigured";
    if (info.status === "very_stale") return "failed";
    return "connected";
  };

  return (
    <main className="mx-auto max-w-6xl space-y-8 px-6 py-8">
      <header className="space-y-1">
        <h1 className="text-3xl font-semibold tracking-tight">Cartuchos</h1>
        <p className="text-sm text-muted-foreground">
          Conecta OMEGA con tus sistemas origen. Cada cartucho expone una
          configuración propia y se prueba en vivo antes de quedar activo.
        </p>
      </header>

      {list.isError ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
          <p className="font-medium text-destructive">
            No se pudieron cargar los cartuchos.
          </p>
          <button
            type="button"
            onClick={() => list.refetch()}
            className="mt-2 text-xs font-medium text-destructive underline-offset-2 hover:underline"
          >
            Reintentar
          </button>
        </div>
      ) : null}

      <section
        aria-label="Listado de cartuchos"
        // v1.44.3 R1 Frontend P2: dropped the redundant lg:grid-cols-2
        // — it was identical to md:grid-cols-2. 2-column at md and up
        // is the intentional layout; bump to 3 once the brief adds a
        // 5th cartridge.
        className="grid grid-cols-1 gap-4 md:grid-cols-2"
      >
        {list.isLoading
          ? Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className="h-40 animate-pulse rounded-lg border bg-card"
                aria-hidden
              />
            ))
          : (list.data?.cartridges ?? []).map((id) => (
              <CartridgeCard
                key={id}
                id={id}
                name={META[id]?.name ?? id}
                description={META[id]?.description ?? ""}
                status={statusFor(id)}
              />
            ))}
      </section>
    </main>
  );
}
