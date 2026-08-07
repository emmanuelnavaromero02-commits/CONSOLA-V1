"use client";

import Link from "next/link";
import { useMemo } from "react";
import { AlertTriangle, ArrowLeft, Lock } from "lucide-react";

import {
  appCartridgeId,
  isPublishedAppName,
  useAnalyticsApps,
} from "@/lib/hooks/useAnalyticsApps";
import { isApiError } from "@/lib/api";

function ViewerFrame({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto flex max-w-3xl flex-col items-start gap-4 px-6 py-12">
      {children}
      <Link
        href="/analytics"
        className="inline-flex min-h-[44px] items-center gap-2 rounded-md border px-4 text-sm font-medium hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ArrowLeft className="h-4 w-4" /> Volver al catálogo
      </Link>
    </main>
  );
}

export function AppViewer({ appName }: { appName: string | null }) {
  const requested = (appName ?? "").trim();
  const validName = isPublishedAppName(requested);

  // The catalog is the authority on what this caller may open. A name that the
  // API did not return is treated as not found, so the viewer can never be used
  // to probe for apps outside the caller's scope.
  const { data, isLoading, isError, error } = useAnalyticsApps();
  const app = useMemo(
    () => (validName ? data?.apps?.find((item) => item.name === requested) : undefined),
    [data, requested, validName],
  );

  if (!validName) {
    return (
      <ViewerFrame>
        <h1 className="text-xl font-semibold">Aplicación no válida</h1>
        <p role="alert" className="text-sm text-muted-foreground">
          La dirección no identifica una aplicación publicada.
        </p>
      </ViewerFrame>
    );
  }

  if (isLoading) {
    return (
      <main className="mx-auto max-w-[1600px] px-6 py-8" aria-busy="true">
        <div className="h-8 w-64 animate-pulse rounded bg-muted/60" />
        <div className="mt-4 h-[70vh] animate-pulse rounded-lg border bg-muted/40" />
      </main>
    );
  }

  if (isError) {
    const status = isApiError(error) ? error.status : undefined;
    const forbidden = status === 401 || status === 403;
    return (
      <ViewerFrame>
        <h1 className="text-xl font-semibold">
          {forbidden ? "Sin acceso" : "No se pudo abrir la aplicación"}
        </h1>
        <p role="alert" className="flex items-center gap-2 text-sm text-muted-foreground">
          {forbidden ? <Lock className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
          {forbidden
            ? "Tu usuario no tiene acceso a las aplicaciones de este workspace."
            : "Vuelve a intentarlo desde el catálogo."}
        </p>
      </ViewerFrame>
    );
  }

  if (!app) {
    return (
      <ViewerFrame>
        <h1 className="text-xl font-semibold">Aplicación no disponible</h1>
        <p role="alert" className="text-sm text-muted-foreground">
          No está publicada para este workspace, o su cartucho no tiene conexión activa.
        </p>
      </ViewerFrame>
    );
  }

  const title = (app.title ?? "").trim() || app.name.replace(/_/g, " ");
  const cartridge = appCartridgeId(app);
  const missing = app.unavailable_datasets ?? [];

  return (
    <main className="mx-auto flex h-[calc(100vh-4rem)] max-w-[1600px] flex-col gap-3 px-4 py-4 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          {/* The app renders its own <h1>; the shell header stays a breadcrumb
              so the viewer does not stack two competing titles. */}
          <Link
            href="/analytics"
            className="inline-flex min-h-[44px] items-center gap-2 text-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ArrowLeft className="h-4 w-4" /> Analytics
          </Link>
          <p className="truncate text-xs text-muted-foreground">
            {cartridge ? `${cartridge} · ` : ""}
            {title}
          </p>
        </div>
      </div>

      {missing.length ? (
        <p
          role="alert"
          className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-500/10 dark:text-amber-200"
        >
          Datos incompletos: faltan {missing.length} dataset
          {missing.length === 1 ? "" : "s"}. Las secciones afectadas aparecerán vacías.
        </p>
      ) : null}

      <iframe
        // Always the wrapper, never the app. `/apps/{name}` returns published
        // HTML authored outside this repo; `/apps/{name}/embed` returns a
        // first-party document we generate, which then hosts that HTML in its
        // own sandboxed child. Loading the app directly here would run
        // untrusted markup at the console's origin.
        //
        // The sandbox below is not the security boundary — it cannot be, since
        // the wrapper needs `allow-same-origin` to read the CSRF cookie and
        // make credentialed calls on the user's behalf. The real boundary is
        // one level down: the wrapper's inner iframe is `sandbox="allow-scripts"`
        // with no `allow-same-origin`, so the app holds an opaque origin and
        // reaches data only through the postMessage broker. This attribute is
        // defence in depth over trusted markup; `allow-downloads` is gone
        // because the wrapper has no reason to start one.
        //
        // Server-side name validation happens again in /apps/{name}/embed; this
        // encode keeps the client from building anything but a same-origin path.
        src={`/apps/${encodeURIComponent(app.name)}/embed`}
        title={title}
        className="min-h-[60vh] flex-1 rounded-lg border bg-background"
        sandbox="allow-scripts allow-same-origin"
        referrerPolicy="same-origin"
      />
    </main>
  );
}
