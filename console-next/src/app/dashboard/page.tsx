"use client";

import { useKpis } from "@/lib/hooks/useKpis";
import { KpiCard } from "@/components/dashboard/KpiCard";
import { FreshnessTable } from "@/components/dashboard/FreshnessTable";
import { LogoutButton } from "@/components/auth/LogoutButton";

/**
 * v1.44.3.3 R-Mac Mini-fix: backend KPI counts come through
 * the JSON envelope as numbers (``int(...)`` in
 * console/app/routers/dashboard.py), but a stale cache /
 * Decimal-string DB driver / forgotten conversion downstream
 * can produce strings that visually render the same but
 * cause E2E "value is a number" assertions to fail. Force
 * every count through ``Number(x ?? 0)`` so the render path
 * is type-stable, and pass the raw numeric to the KpiCard via
 * ``numericValue`` for E2E extraction.
 */
function coerceNumber(v: unknown): number {
  const n = Number(v ?? 0);
  return Number.isFinite(n) ? n : 0;
}

export default function DashboardPage() {
  const { data, isLoading, isError, refetch } = useKpis();

  const freshnessRows = data
    ? Object.entries(data.data_freshness).map(([cartridge, info]) => ({
        cartridge,
        ageHours: info.age_hours,
        status:   info.status,
      }))
    : [];

  // Pre-coerce every numeric we render so the card values are
  // guaranteed to be JavaScript numbers (not "5"-as-string).
  const cartridgesTotal        = coerceNumber(data?.cartridges.total);
  const cartridgesConnected    = coerceNumber(data?.cartridges.connected);
  const cartridgesDisconnected = coerceNumber(data?.cartridges.disconnected);
  const extractionsToday       = coerceNumber(data?.extractions.today);
  const extractionsWeek        = coerceNumber(data?.extractions.week);
  const usersActiveToday       = coerceNumber(data?.users.active_today);
  const usersTotal             = coerceNumber(data?.users.total);
  const copilotToolsToday      = coerceNumber(data?.copilot.tools_invoked_today);
  const copilotConvsToday      = coerceNumber(data?.copilot.conversations_today);
  const auditEventsToday       = coerceNumber(data?.audit.events_today);
  const auditDestructiveToday  = coerceNumber(data?.audit.destructive_actions_today);

  return (
    <main className="mx-auto max-w-6xl space-y-8 px-6 py-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <h1 className="text-3xl font-semibold tracking-tight">Panel</h1>
          <p className="text-sm text-muted-foreground">
            Estado en tiempo real de cartuchos, extracciones y copiloto.
          </p>
        </div>
        {/* v1.44.3.3 Task E — logout affordance the 01-login-deep
            spec was flagging as a known v1.44.4 deficit. */}
        <LogoutButton />
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
          // Display "5 / 10"; expose the connected count as the
          // numeric token tests key off (the more important number
          // of the two).
          value={
            data
              ? `${cartridgesConnected} / ${cartridgesTotal}`
              : "—"
          }
          numericValue={data ? cartridgesConnected : undefined}
          hint={
            data && cartridgesDisconnected > 0
              ? `${cartridgesDisconnected} sin conexión`
              : "Todos en línea"
          }
          trend={data && cartridgesDisconnected === 0 ? "up" : "flat"}
          loading={isLoading && !data}
        />
        <KpiCard
          label="Extracciones hoy"
          value={data ? extractionsToday : "—"}
          numericValue={data ? extractionsToday : undefined}
          hint={data ? `${extractionsWeek} esta semana` : ""}
          loading={isLoading && !data}
        />
        <KpiCard
          label="Usuarios activos"
          value={data ? usersActiveToday : "—"}
          numericValue={data ? usersActiveToday : undefined}
          hint={data ? `${usersTotal} en total` : ""}
          loading={isLoading && !data}
        />
        <KpiCard
          label="Acciones copiloto"
          value={data ? copilotToolsToday : "—"}
          numericValue={data ? copilotToolsToday : undefined}
          hint={
            data
              ? `${copilotConvsToday} conversaciones`
              : ""
          }
          loading={isLoading && !data}
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
          value={data ? auditEventsToday : "—"}
          numericValue={data ? auditEventsToday : undefined}
          loading={isLoading && !data}
        />
        <KpiCard
          label="Acciones destructivas"
          value={data ? auditDestructiveToday : "—"}
          numericValue={data ? auditDestructiveToday : undefined}
          hint={
            data && auditDestructiveToday > 0
              ? "Revisar audit log"
              : "Sin movimientos"
          }
          trend={
            data && auditDestructiveToday > 0 ? "down" : "flat"
          }
          loading={isLoading && !data}
        />
      </section>
    </main>
  );
}
