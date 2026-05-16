"use client";

import { useKpis } from "@/lib/hooks/useKpis";
import { KpiCard } from "@/components/dashboard/KpiCard";
import { FreshnessTable } from "@/components/dashboard/FreshnessTable";

export default function DashboardPage() {
  const { data, isLoading, isError, refetch } = useKpis();

  const freshnessRows = data
    ? Object.entries(data.data_freshness).map(([cartridge, info]) => ({
        cartridge,
        ageHours: info.age_hours,
        status:   info.status,
      }))
    : [];

  return (
    <main className="mx-auto max-w-6xl space-y-8 px-6 py-8">
      <header className="space-y-1">
        <h1 className="text-3xl font-semibold tracking-tight">Panel</h1>
        <p className="text-sm text-muted-foreground">
          Estado en tiempo real de cartuchos, extracciones y copiloto.
        </p>
      </header>

      {isError ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
          <p className="font-medium text-destructive">
            No se pudieron cargar los indicadores.
          </p>
          <button
            type="button"
            onClick={() => refetch()}
            className="mt-2 text-xs font-medium text-destructive underline-offset-2 hover:underline"
          >
            Reintentar
          </button>
        </div>
      ) : null}

      <section
        aria-label="Indicadores clave"
        className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4"
      >
        <KpiCard
          label="Cartuchos conectados"
          value={
            data
              ? `${data.cartridges.connected} / ${data.cartridges.total}`
              : "—"
          }
          hint={
            data && data.cartridges.disconnected > 0
              ? `${data.cartridges.disconnected} sin conexión`
              : "Todos en línea"
          }
          trend={data && data.cartridges.disconnected === 0 ? "up" : "flat"}
          loading={isLoading}
        />
        <KpiCard
          label="Extracciones hoy"
          value={data ? data.extractions.today : "—"}
          hint={data ? `${data.extractions.week} esta semana` : ""}
          loading={isLoading}
        />
        <KpiCard
          label="Usuarios activos"
          value={data ? data.users.active_today : "—"}
          hint={data ? `${data.users.total} en total` : ""}
          loading={isLoading}
        />
        <KpiCard
          label="Acciones copiloto"
          value={data ? data.copilot.tools_invoked_today : "—"}
          hint={
            data
              ? `${data.copilot.conversations_today} conversaciones`
              : ""
          }
          loading={isLoading}
        />
      </section>

      <section aria-label="Frescura de datos">
        <FreshnessTable rows={freshnessRows} loading={isLoading} />
      </section>

      <section
        aria-label="Auditoría"
        className="grid grid-cols-1 gap-4 md:grid-cols-2"
      >
        <KpiCard
          label="Eventos hoy"
          value={data ? data.audit.events_today : "—"}
          loading={isLoading}
        />
        <KpiCard
          label="Acciones destructivas"
          value={data ? data.audit.destructive_actions_today : "—"}
          hint={
            data && data.audit.destructive_actions_today > 0
              ? "Revisar audit log"
              : "Sin movimientos"
          }
          trend={
            data && data.audit.destructive_actions_today > 0 ? "down" : "flat"
          }
          loading={isLoading}
        />
      </section>
    </main>
  );
}
