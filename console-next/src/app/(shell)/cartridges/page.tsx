"use client";

import { useKpis } from "@/lib/hooks/useKpis";
import { useActivateCartridge, useCartridgeList } from "@/lib/hooks/useCartridges";
import { CartridgeCard } from "@/components/cartridges/CartridgeCard";
import type { ConnectionStatus } from "@/components/cartridges/StatusBadge";
import { toast } from "sonner";

const META: Record<
  string,
  { name: string; description: string }
> = {
  replicon: {
    name: "Replicon",
    description: "Time tracking + project hours. Empleados, proyectos, time entries.",
  },
  "hubspot": {
    name: "HubSpot CRM",
    description: "CRM comercial. Deals, empresas, contactos, pipeline y forecast.",
  },
  banxico: {
    name: "Banxico SIE",
    description: "Series macro oficiales. Bronze, provenance y manifest.",
  },
  inegi: {
    name: "INEGI",
    description: "Indicadores oficiales. Silver/Gold gobernado para contexto macro.",
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
 * cartridge. The mapping is honest — freshness is reported as
 * freshness, never collapsed into connection success/failure:
 *   fresh      → connected    (recent successful run)
 *   stale      → stale        ("Datos antiguos", ámbar)
 *   very_stale → very_stale   ("Datos muy antiguos"; antigüedad ≠ fallo)
 *   never      → unconfigured (no successful run on record)
 *
 * "untested" — credentials saved but not yet probed — needs a
 * separate vault-read endpoint that v1.44.3 doesn't yet expose;
 * we conservatively report "untested" on the detail page after a
 * save action but never mark a grid tile that way for now.
 */
export default function CartridgesPage() {
  const list = useCartridgeList();
  const kpis = useKpis();
  const activate = useActivateCartridge();

  const statusFor = (id: string): ConnectionStatus => {
    const info = kpis.data?.data_freshness?.[id];
    if (!info) return "unconfigured";
    if (info.status === "never") return "unconfigured";
    if (info.status === "very_stale") return "very_stale";
    if (info.status === "stale") return "stale";
    return "connected";
  };

  const activateOne = async (id: string) => {
    try {
      const result = await activate.mutateAsync(id);
      const status = result.installation?.status || result.installation?.access_status || "solicitado";
      toast.success(`${META[id]?.name ?? id}: activación enviada (${status}).`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "No se pudo activar el cartucho.");
    }
  };

  return (
    <main className="mx-auto max-w-6xl space-y-8 px-6 py-8">
      <header className="space-y-1">
        <h1 className="text-3xl font-semibold tracking-tight">Monitor técnico de cartuchos</h1>
        <p className="text-sm text-muted-foreground">
          Supervisa conectividad, pruebas en vivo y estado técnico de los
          cartuchos ya integrados.
        </p>
      </header>

      {list.isError ? (
        <div
          role="alert"
          aria-live="polite"
          className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm"
        >
          <p className="font-medium text-destructive">
            No se pudieron cargar los cartuchos.
          </p>
          <button
            type="button"
            onClick={() => list.refetch()}
            className="mt-2 inline-flex min-h-[44px] items-center justify-center rounded-md border border-destructive/40 px-3 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40"
          >
            Reintentar
          </button>
        </div>
      ) : null}

      <section
        aria-label="Listado de cartuchos"
        aria-busy={list.isLoading}
        className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3"
      >
        {list.isLoading
          ? Array.from({ length: 5 }).map((_, i) => (
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
                ageHours={kpis.data?.data_freshness?.[id]?.age_hours ?? null}
                activating={activate.isPending && activate.variables === id}
                onActivate={activateOne}
              />
            ))}
      </section>
    </main>
  );
}
