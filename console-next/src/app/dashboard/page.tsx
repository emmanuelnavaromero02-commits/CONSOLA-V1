"use client";

import Link from "next/link";

import { useKpis } from "@/lib/hooks/useKpis";
import { KpiCard } from "@/components/dashboard/KpiCard";
import { FreshnessTable } from "@/components/dashboard/FreshnessTable";
import { BriefingSection } from "@/components/dashboard/BriefingSection";
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
        <div className="flex flex-wrap items-center gap-2">
          {/* v1.44.4 Task A — surface the new /workspace entry
              point here so a returning operator can jump
              straight into the copilot without the AppChrome
              nav (which lands in Task H). */}
          <Link
            href="/workspace"
            className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span aria-hidden className="mr-1.5">💬</span>
            Ir al copiloto
          </Link>
          {/* v1.44.3.3 Task E — logout affordance the 01-login-deep
              spec was flagging as a known v1.44.4 deficit. */}
          <LogoutButton />
        </div>
      </header>

      {/* v1.44.4 Task B — proactive briefing surfaced ABOVE the
          KPIs so a returning operator sees actionable alerts the
          moment they land. Polls every 60 s. */}
      <BriefingSection />

      {isError ? (
        // v1.44.3.3 R-Mac-Round-3 Task E: ``role="alert"`` so
        // screen readers announce the failure; retry button
        // bumped to min-h-[44px] (was a text-link); aria-live
        // marked polite so a transient refetch error doesn't
        // hijack focus.
        <div
          role="alert"
          aria-live="polite"
          className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm"
        >
          <p className="font-medium text-destructive">
            No se pudieron cargar los indicadores.
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
        aria-label="Indicadores clave"
        // v1.44.3.3 R-Mac-Round-3 Task E: ``sm:`` (≥640 px) for
        // the first split so phones in landscape get two
        // columns, not four-wide squished cards. Behaviour on
        // ≥md is unchanged.
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
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
          numericValue={data ? cartridgesConnected : 0}
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
          numericValue={data ? extractionsToday : 0}
          hint={data ? `${extractionsWeek} esta semana` : ""}
          loading={isLoading && !data}
        />
        <KpiCard
          label="Usuarios activos"
          value={data ? usersActiveToday : "—"}
          numericValue={data ? usersActiveToday : 0}
          hint={data ? `${usersTotal} en total` : ""}
          loading={isLoading && !data}
        />
        <KpiCard
          label="Acciones copiloto"
          value={data ? copilotToolsToday : "—"}
          numericValue={data ? copilotToolsToday : 0}
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
          numericValue={data ? auditEventsToday : 0}
          loading={isLoading && !data}
        />
        <KpiCard
          label="Acciones destructivas"
          value={data ? auditDestructiveToday : "—"}
          numericValue={data ? auditDestructiveToday : 0}
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
