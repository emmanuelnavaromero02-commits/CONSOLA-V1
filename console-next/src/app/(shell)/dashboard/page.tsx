"use client";

import Link from "next/link";

import { useKpis } from "@/lib/hooks/useKpis";
import { toKpiView, type KpiView } from "@/lib/hooks/kpi-view";
import { KpiCard } from "@/components/dashboard/KpiCard";
import { DashboardErrorBoundary } from "@/components/dashboard/DashboardErrorBoundary";
import { FreshnessTable } from "@/components/dashboard/FreshnessTable";
import { BriefingSection } from "@/components/dashboard/BriefingSection";
import { LogoutButton } from "@/components/auth/LogoutButton";

function claimHint(
  raw: number | null,
  view: KpiView | null,
  isError: boolean,
  positive: string,
  negative: (count: number) => string,
): string {
  if (!view) return isError ? "No disponible" : "";
  if (isError) return "Último dato disponible; actualización fallida";
  if (raw == null) return "Sin datos";
  return raw > 0 ? negative(raw) : positive;
}

function countValue(raw: number | null, view: KpiView | null): string | number {
  if (!view) return "—";
  return raw == null ? "Sin datos" : raw;
}

export default function DashboardPage() {
  return (
    <DashboardErrorBoundary>
      <DashboardContent />
    </DashboardErrorBoundary>
  );
}

function DashboardContent() {
  const { data, isLoading, isError, refetch } = useKpis();
  const view = toKpiView(data);
  const freshnessRows = view?.freshnessRows ?? [];

  return (
    <main className="mx-auto max-w-6xl space-y-8 px-6 py-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <h1 className="text-3xl font-semibold tracking-tight">Panel</h1>
          <p className="text-sm text-muted-foreground">
            Estado de cartuchos, extracciones y copiloto (actualizado cada 30 s).
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link
            href="/workspace"
            className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span aria-hidden className="mr-1.5">💬</span>
            Ir al workspace
          </Link>
          <LogoutButton />
        </div>
      </header>

      <BriefingSection />

      {isError ? (
        <div
          role="alert"
          aria-live="polite"
          className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm"
        >
          <p className="font-medium text-destructive">
            No se pudieron cargar los indicadores.
            {view ? " Se muestra el último dato disponible." : ""}
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
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
      >
        <KpiCard
          label="Cartuchos conectados"
          value={
            view
              ? view.cartridges.connected != null && view.cartridges.total != null
                ? `${view.cartridges.connected} / ${view.cartridges.total}`
                : "Sin datos"
              : "—"
          }
          numericValue={view?.cartridges.connected ?? undefined}
          hint={claimHint(
            view?.cartridges.disconnected ?? null,
            view,
            isError,
            "Todos en línea",
            (count) => `${count} sin conexión`,
          )}
          trend={!isError && view?.cartridges.disconnected === 0 ? "up" : "flat"}
          loading={isLoading && !view}
        />
        <KpiCard
          label="Extracciones hoy"
          value={countValue(view?.extractions.today ?? null, view)}
          numericValue={view?.extractions.today ?? undefined}
          hint={view && view.extractions.week != null ? `${view.extractions.week} esta semana` : ""}
          loading={isLoading && !view}
        />
        <KpiCard
          label="Usuarios activos"
          value={countValue(view?.users.activeToday ?? null, view)}
          numericValue={view?.users.activeToday ?? undefined}
          hint={view && view.users.total != null ? `${view.users.total} en total` : ""}
          loading={isLoading && !view}
        />
        <KpiCard
          label="Acciones copiloto"
          value={countValue(view?.copilot.toolsToday ?? null, view)}
          numericValue={view?.copilot.toolsToday ?? undefined}
          hint={
            view && view.copilot.convsToday != null
              ? `${view.copilot.convsToday} conversaciones`
              : ""
          }
          loading={isLoading && !view}
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
          value={countValue(view?.audit.eventsToday ?? null, view)}
          numericValue={view?.audit.eventsToday ?? undefined}
          loading={isLoading && !view}
        />
        <KpiCard
          label="Acciones destructivas"
          value={countValue(view?.audit.destructiveToday ?? null, view)}
          numericValue={view?.audit.destructiveToday ?? undefined}
          hint={claimHint(
            view?.audit.destructiveToday ?? null,
            view,
            isError,
            "Sin movimientos",
            () => "Revisar audit log",
          )}
          trend={
            typeof view?.audit.destructiveToday === "number" && view.audit.destructiveToday > 0
              ? "down"
              : "flat"
          }
          loading={isLoading && !view}
        />
      </section>
    </main>
  );
}
