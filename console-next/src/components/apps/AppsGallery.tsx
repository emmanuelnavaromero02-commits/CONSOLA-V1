"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, RefreshCw, Trash2 } from "lucide-react";

import { deleteApp, listApps } from "@/lib/admin-surfaces";

function updated(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace("T", " ");
  return date.toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" });
}

export function AppsGallery() {
  const queryClient = useQueryClient();
  const apps = useQuery({
    queryKey: ["apps-gallery"],
    queryFn: listApps,
    staleTime: 30_000,
  });
  const remove = useMutation({
    mutationFn: deleteApp,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["apps-gallery"] }),
  });

  if (apps.isLoading) {
    return (
      <div aria-busy="true" className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <span key={i} className="block h-40 animate-pulse rounded-lg bg-muted" aria-hidden />
        ))}
      </div>
    );
  }

  if (apps.isError) {
    return (
      <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
        <p className="font-medium text-destructive">No se pudieron cargar las apps.</p>
        <button
          type="button"
          onClick={() => apps.refetch()}
          className="mt-2 inline-flex min-h-[44px] items-center gap-2 rounded-md border px-3 text-xs font-medium"
        >
          <RefreshCw aria-hidden className="h-4 w-4" />
          Reintentar
        </button>
      </div>
    );
  }

  const rows = apps.data ?? [];

  if (rows.length === 0) {
    return (
      <p className="rounded-md border bg-muted/30 p-6 text-sm text-muted-foreground">
        No hay aplicaciones configuradas para las conexiones activas del workspace.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => apps.refetch()}
          className="inline-flex min-h-[44px] items-center gap-2 rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RefreshCw aria-hidden className="h-4 w-4" />
          Refrescar
        </button>
      </div>
      <section aria-label="Apps publicadas" className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {rows.map((app) => (
          <article key={app.name} className="flex min-h-44 flex-col gap-3 rounded-lg border bg-card p-5">
            <div className="flex items-start justify-between gap-3">
              <div className="space-y-1">
                <h2 className="text-base font-semibold tracking-tight">{app.title || app.name}</h2>
                <p className="text-xs text-muted-foreground">Actualizada {updated(app.updated_at)}</p>
              </div>
              <span aria-hidden className="rounded-md bg-primary/10 px-2 py-1 text-xs font-semibold text-primary">
                APP
              </span>
            </div>
            <p className="flex-1 text-sm text-muted-foreground">{app.description || "Sin descripción publicada."}</p>
            <div className="flex flex-wrap gap-2">
              <a
                href={`/apps/${encodeURIComponent(app.name)}`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex min-h-[44px] flex-1 items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <ExternalLink aria-hidden className="h-4 w-4" />
                Abrir
              </a>
              <button
                type="button"
                onClick={() => remove.mutate(app.name)}
                disabled={remove.isPending}
                className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md border border-destructive/40 text-destructive hover:bg-destructive/10 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40"
                aria-label={`Eliminar ${app.title || app.name}`}
              >
                <Trash2 aria-hidden className="h-4 w-4" />
              </button>
            </div>
          </article>
        ))}
      </section>
      {remove.isError ? (
        <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          No se pudo eliminar la app.
        </p>
      ) : null}
    </div>
  );
}
