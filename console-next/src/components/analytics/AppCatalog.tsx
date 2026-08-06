"use client";

import Link from "next/link";
import { useMemo } from "react";
import { AlertTriangle, BarChart3, Lock, RefreshCw } from "lucide-react";

import {
  appCartridgeId,
  appDataState,
  cartridgesFromApps,
  useAnalyticsApps,
} from "@/lib/hooks/useAnalyticsApps";
import { isApiError } from "@/lib/api";
import type { AnalyticsApp } from "@/lib/admin-surfaces";

const CARTRIDGE_LABEL: Record<string, string> = {
  sap_successfactors: "SAP SuccessFactors",
  sap_hcm: "SAP HCM",
  sap_s4hana: "SAP S/4HANA",
  salesforce: "Salesforce",
  replicon: "Replicon",
  hubspot: "HubSpot",
};

function cartridgeLabel(id: string): string {
  return CARTRIDGE_LABEL[id] ?? id.replace(/_/g, " ");
}

function appTitle(app: AnalyticsApp): string {
  return (app.title ?? "").trim() || app.name.replace(/_/g, " ");
}

function formatUpdatedAt(value?: string | null): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" });
}

function DataStateBadge({ app }: { app: AnalyticsApp }) {
  const state = appDataState(app);
  if (state === "ready") {
    return (
      <span className="inline-flex items-center rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">
        Datos completos
      </span>
    );
  }
  if (state === "partial") {
    return (
      <span className="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700 dark:bg-amber-500/10 dark:text-amber-300">
        Datos incompletos
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
      Estado no informado
    </span>
  );
}

function AppCard({ app }: { app: AnalyticsApp }) {
  const state = appDataState(app);
  const missing = app.unavailable_datasets ?? [];
  const updated = formatUpdatedAt(app.updated_at);
  const cartridge = appCartridgeId(app);

  return (
    <article className="flex flex-col justify-between rounded-lg border bg-card p-4 shadow-sm">
      <div className="space-y-2">
        <div className="flex items-start justify-between gap-3">
          <h3 className="text-sm font-semibold leading-tight">{appTitle(app)}</h3>
          <DataStateBadge app={app} />
        </div>
        {cartridge ? (
          <p className="text-xs text-muted-foreground">{cartridgeLabel(cartridge)}</p>
        ) : null}
        {app.description ? (
          <p className="text-sm text-muted-foreground">{app.description}</p>
        ) : null}

        <dl className="space-y-1 pt-1 text-xs text-muted-foreground">
          {app.datasets_used?.length ? (
            <div>
              <dt className="inline font-medium">Datasets requeridos: </dt>
              <dd className="inline">{app.datasets_used.length}</dd>
            </div>
          ) : null}
          <div>
            <dt className="inline font-medium">Actualizado: </dt>
            {/* Never invent a timestamp: absence is reported as absence. */}
            <dd className="inline">{updated ?? "Frescura no informada"}</dd>
          </div>
        </dl>

        {state === "partial" && missing.length ? (
          <p className="rounded-md bg-amber-50 px-2 py-1.5 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
            Faltan {missing.length} dataset{missing.length === 1 ? "" : "s"}:{" "}
            <span className="font-mono">{missing.slice(0, 3).join(", ")}</span>
            {missing.length > 3 ? "…" : ""}
          </p>
        ) : null}
      </div>

      <Link
        href={`/analytics/viewer?app=${encodeURIComponent(app.name)}`}
        className="mt-4 inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        Abrir aplicación
      </Link>
    </article>
  );
}

function CatalogSkeleton() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3" aria-busy="true">
      {[0, 1, 2].map((key) => (
        <div key={key} className="h-52 animate-pulse rounded-lg border bg-muted/40" />
      ))}
    </div>
  );
}

export function AppCatalog({ cartridge }: { cartridge?: string }) {
  const { data, isLoading, isError, error, refetch, isFetching } = useAnalyticsApps({
    cartridge,
  });

  const apps = useMemo(() => data?.apps ?? [], [data]);
  const cartridges = useMemo(() => cartridgesFromApps(apps), [apps]);

  if (isLoading) return <CatalogSkeleton />;

  if (isError) {
    const status = isApiError(error) ? error.status : undefined;
    // 401/403 is a scope answer, not a crash: say so without leaking internals.
    const forbidden = status === 401 || status === 403;
    return (
      <div
        role="alert"
        className="flex flex-col items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-4"
      >
        <p className="flex items-center gap-2 text-sm font-medium">
          {forbidden ? <Lock className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
          {forbidden
            ? "No tienes acceso al catálogo de aplicaciones en este workspace."
            : "No se pudo cargar el catálogo de aplicaciones."}
        </p>
        {!forbidden ? (
          <button
            type="button"
            onClick={() => refetch()}
            className="inline-flex min-h-[44px] items-center gap-2 rounded-md border px-4 text-sm font-medium hover:bg-accent"
          >
            <RefreshCw className="h-4 w-4" /> Reintentar
          </button>
        ) : null}
      </div>
    );
  }

  const hiddenUnconfigured = data?.apps_scope?.hidden_unconfigured_count ?? 0;

  if (apps.length === 0) {
    return (
      <div className="rounded-lg border border-dashed p-8 text-center">
        <BarChart3 className="mx-auto h-8 w-8 text-muted-foreground" aria-hidden="true" />
        <p className="mt-3 text-sm font-medium">No hay aplicaciones disponibles.</p>
        <p className="mt-1 text-sm text-muted-foreground">
          {data?.apps_scope?.message ??
            data?.apps_readiness?.message ??
            "Activa un cartucho con aplicaciones publicadas para verlas aquí."}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {cartridges.length > 1 ? (
        <nav className="flex flex-wrap gap-2" aria-label="Filtrar por cartucho">
          <FilterChip href="/analytics" active={!cartridge} label="Todos" />
          {cartridges.map((id) => (
            <FilterChip
              key={id}
              href={`/analytics?cartridge=${encodeURIComponent(id)}`}
              active={cartridge === id}
              label={cartridgeLabel(id)}
            />
          ))}
        </nav>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {apps.map((app) => (
          <AppCard key={app.name} app={app} />
        ))}
      </div>

      {hiddenUnconfigured > 0 ? (
        <p className="text-xs text-muted-foreground">
          {hiddenUnconfigured} aplicación{hiddenUnconfigured === 1 ? "" : "es"} oculta
          {hiddenUnconfigured === 1 ? "" : "s"} porque su cartucho no tiene conexión activa.
        </p>
      ) : null}
      {isFetching ? (
        <p className="text-xs text-muted-foreground" aria-live="polite">
          Actualizando…
        </p>
      ) : null}
    </div>
  );
}

function FilterChip({
  href,
  active,
  label,
}: {
  href: string;
  active: boolean;
  label: string;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={`inline-flex min-h-[44px] items-center rounded-full border px-4 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
        active ? "border-primary bg-primary/10 text-primary" : "hover:bg-accent"
      }`}
    >
      {label}
    </Link>
  );
}
