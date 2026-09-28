"use client";

import Link from "next/link";
import { AlertTriangle, Lock } from "lucide-react";

import type { AnalyticsApp } from "@/lib/admin-surfaces";
import { isApiError } from "@/lib/api";
import { useAnalyticsApps } from "@/lib/hooks/useAnalyticsApps";

export function appGalleryOrigin(app: AnalyticsApp): "workspace" | "cartridge" {
  return (app.origin ?? "").trim() === "workspace" ? "workspace" : "cartridge";
}

function appTitle(app: AnalyticsApp): string {
  return (app.title ?? "").trim() || app.name.replace(/_/g, " ");
}

function AppCard({ app }: { app: AnalyticsApp }) {
  const description = (app.description ?? "").trim();
  return (
    <article className="flex flex-col justify-between rounded-lg border bg-card p-4 shadow-sm">
      <div className="space-y-2">
        <h3 className="text-sm font-semibold leading-tight">{appTitle(app)}</h3>
        <p className="text-sm text-muted-foreground">
          {description || "Sin información"}
        </p>
        <p className="text-xs text-muted-foreground">
          Datasets requeridos:{" "}
          {app.datasets_used?.length ? app.datasets_used.length : "Sin información"}
        </p>
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

function GallerySection({
  title,
  apps,
  emptyState,
}: {
  title: string;
  apps: AnalyticsApp[];
  emptyState: string;
}) {
  return (
    <section aria-label={title} className="space-y-3">
      <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
      {apps.length ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {apps.map((app) => (
            <AppCard key={app.name} app={app} />
          ))}
        </div>
      ) : (
        <p className="rounded-lg border border-dashed bg-card/50 px-4 py-6 text-sm text-muted-foreground">
          {emptyState}
        </p>
      )}
    </section>
  );
}

export interface AppsGalleryViewProps {
  apps: AnalyticsApp[];
  isLoading?: boolean;
  isError?: boolean;
  forbidden?: boolean;
}

export function AppsGalleryView({
  apps,
  isLoading = false,
  isError = false,
  forbidden = false,
}: AppsGalleryViewProps) {
  if (isLoading) {
    return (
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3" aria-busy="true">
        {[0, 1, 2].map((key) => (
          <div key={key} className="h-44 animate-pulse rounded-lg border bg-muted/40" />
        ))}
      </div>
    );
  }
  if (isError) {
    return (
      <p role="alert" className="flex items-center gap-2 text-sm text-muted-foreground">
        {forbidden ? <Lock className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
        {forbidden
          ? "No tienes acceso a las aplicaciones de este workspace."
          : "No se pudo cargar la galería de aplicaciones. Vuelve a intentarlo."}
      </p>
    );
  }
  const workspaceApps = apps.filter((app) => appGalleryOrigin(app) === "workspace");
  const cartridgeApps = apps.filter((app) => appGalleryOrigin(app) === "cartridge");
  return (
    <div className="space-y-8">
      <GallerySection
        title="Creadas en este workspace"
        apps={workspaceApps}
        emptyState="Aún no hay aplicaciones creadas en este workspace. Pídele una al Copiloto describiendo el objetivo y los datasets."
      />
      <GallerySection
        title="Aplicaciones de fuentes de datos"
        apps={cartridgeApps}
        emptyState="No hay aplicaciones de fuentes de datos disponibles para las conexiones activas de este workspace."
      />
    </div>
  );
}

export function AppsGallery() {
  const { data, isLoading, isError, error } = useAnalyticsApps();
  const status = isApiError(error) ? error.status : undefined;
  return (
    <AppsGalleryView
      apps={data?.apps ?? []}
      isLoading={isLoading}
      isError={isError}
      forbidden={status === 401 || status === 403}
    />
  );
}
