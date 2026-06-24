"use client";

import { ExternalLink, Loader2, Maximize2, RefreshCcw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { OperationalNotice } from "./StatusBadge";
import type { AnalyticsApp, AppsResponse } from "@/lib/admin-surfaces";
import { cn } from "@/lib/utils";

function updated(value?: string | null): string {
  if (!value) return "sin fecha";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace("T", " ");
  return date.toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" });
}

function appLabel(app: AnalyticsApp): string {
  return app.title || app.name.replaceAll("_", " ");
}

export function AnalyticAppsPanel({
  payload,
  loading,
  error,
  selectedApp,
  onSelectedApp,
  onRefresh,
}: {
  payload: AppsResponse | null;
  loading: boolean;
  error: string;
  selectedApp: string;
  onSelectedApp: (name: string) => void;
  onRefresh: () => void;
}) {
  const apps = payload?.apps ?? [];
  const activeApp = useMemo(
    () => apps.find((app) => app.name === selectedApp) || apps[0] || null,
    [apps, selectedApp],
  );
  const [frameLoaded, setFrameLoaded] = useState(false);
  const [frameSlow, setFrameSlow] = useState(false);

  useEffect(() => {
    if (!activeApp?.name) return;
    if (selectedApp !== activeApp.name) onSelectedApp(activeApp.name);
  }, [activeApp?.name, onSelectedApp, selectedApp]);

  useEffect(() => {
    setFrameLoaded(false);
    setFrameSlow(false);
    if (!activeApp?.name) return undefined;
    const timer = window.setTimeout(() => setFrameSlow(true), 12_000);
    return () => window.clearTimeout(timer);
  }, [activeApp?.name]);

  const emptyMessage =
    payload?.apps_readiness?.message ||
    payload?.apps_scope?.message ||
    "No hay apps analíticas listas para las conexiones activas del workspace.";

  return (
    <section className="rounded-xl border bg-card shadow-sm dark:border-sky-400/20 dark:bg-[#081423] dark:shadow-[0_0_30px_rgba(14,165,233,0.10)]" aria-label="Apps analíticas embebidas">
      <div className="flex flex-col gap-3 border-b p-4 dark:border-sky-400/15 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">Apps analíticas</p>
          <h2 className="mt-1 text-lg font-semibold text-foreground dark:text-white">Análisis embebido del frente activo</h2>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Gráficas publicadas con datos Gold listos; se muestran una a la vez para inspección.
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex min-h-[40px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-semibold text-foreground hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300 dark:border-sky-400/20 dark:bg-[#07111e]"
        >
          <RefreshCcw aria-hidden className={cn("h-4 w-4", loading ? "animate-spin" : "")} />
          Refrescar apps
        </button>
      </div>

      <div className="space-y-4 p-4">
        {error ? <OperationalNotice tone="error" title="Apps no disponibles">{error}</OperationalNotice> : null}
        {loading && !apps.length ? (
          <div className="grid gap-3 md:grid-cols-3">
            {Array.from({ length: 3 }).map((_, index) => (
              <span key={index} aria-hidden className="h-20 animate-pulse rounded-lg bg-muted/60 dark:bg-slate-800/60" />
            ))}
          </div>
        ) : null}
        {!loading && !apps.length ? <OperationalNotice tone="info" title="Sin apps listas">{emptyMessage}</OperationalNotice> : null}

        {apps.length ? (
          <>
            <div className="flex gap-2 overflow-x-auto pb-1" role="tablist" aria-label="Seleccionar app analítica">
              {apps.map((app) => (
                <button
                  type="button"
                  key={app.name}
                  role="tab"
                  aria-selected={activeApp?.name === app.name}
                  onClick={() => onSelectedApp(app.name)}
                  className={cn(
                    "min-h-[44px] shrink-0 rounded-md border px-3 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300",
                    activeApp?.name === app.name
                      ? "border-cyan-400 bg-cyan-500/10 text-cyan-700 dark:text-cyan-200"
                      : "border-slate-200 bg-background text-muted-foreground hover:text-foreground dark:border-sky-400/15 dark:bg-[#07111e]",
                  )}
                >
                  <span className="block max-w-[260px] truncate font-semibold">{appLabel(app)}</span>
                  <span className="block max-w-[260px] truncate text-xs opacity-75">
                    {app.datasets_used?.length ? `${app.datasets_used.length} datasets` : "dataset no declarado"} · {updated(app.updated_at)}
                  </span>
                </button>
              ))}
            </div>

            {activeApp ? (
              <div className="overflow-hidden rounded-lg border bg-background dark:border-sky-400/15 dark:bg-[#06111f]">
                <div className="flex flex-col gap-2 border-b px-4 py-3 dark:border-sky-400/15 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <h3 className="truncate text-base font-semibold text-foreground dark:text-white">{appLabel(activeApp)}</h3>
                    <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{activeApp.description || "App publicada para análisis operativo."}</p>
                  </div>
                  <a
                    href={`/apps/${encodeURIComponent(activeApp.name)}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex min-h-[40px] shrink-0 items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-semibold text-foreground hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300 dark:border-sky-400/20 dark:bg-[#07111e]"
                  >
                    <Maximize2 aria-hidden className="h-4 w-4" />
                    Abrir completa
                    <ExternalLink aria-hidden className="h-3.5 w-3.5" />
                  </a>
                </div>
                <div className="relative min-h-[560px] bg-[#07111e]">
                  {!frameLoaded ? (
                    <div className="absolute inset-0 z-10 grid place-items-center bg-[#07111e] text-sm text-slate-300">
                      <div className="flex items-center gap-2">
                        <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
                        Cargando app analítica...
                      </div>
                    </div>
                  ) : null}
                  {frameSlow && !frameLoaded ? (
                    <div className="absolute inset-x-4 top-4 z-20">
                      <OperationalNotice tone="warning" title="La app tarda más de lo normal">
                        Si el contenido no aparece, abre la app completa para revisar dependencias de datos.
                      </OperationalNotice>
                    </div>
                  ) : null}
                  <iframe
                    key={activeApp.name}
                    title={appLabel(activeApp)}
                    src={`/apps/${encodeURIComponent(activeApp.name)}/embed`}
                    onLoad={() => setFrameLoaded(true)}
                    className="block h-[min(78vh,760px)] min-h-[560px] w-full border-0"
                    referrerPolicy="same-origin"
                  />
                </div>
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </section>
  );
}
