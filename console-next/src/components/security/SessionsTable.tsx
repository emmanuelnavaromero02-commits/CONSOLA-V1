"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, ShieldOff } from "lucide-react";

import { listSecuritySessions, revokeSecuritySession, type SecuritySession } from "@/lib/admin-surfaces";

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" });
}

function sessionKey(session: SecuritySession, index: number): string {
  return session.session_id || session.token_preview || String(session.user_id ?? index);
}

function revocableId(session: SecuritySession): string | null {
  return session.session_id || null;
}

export function SessionsTable() {
  const queryClient = useQueryClient();
  const sessions = useQuery({
    queryKey: ["security", "sessions"],
    queryFn: listSecuritySessions,
    staleTime: 30_000,
  });
  const revoke = useMutation({
    mutationFn: revokeSecuritySession,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["security", "sessions"] }),
  });

  if (sessions.isLoading) {
    return (
      <div aria-busy="true" className="space-y-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <span key={i} className="block h-14 animate-pulse rounded bg-muted" aria-hidden />
        ))}
      </div>
    );
  }

  if (sessions.isError) {
    return (
      <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
        <p className="font-medium text-destructive">No se pudieron cargar las sesiones.</p>
        <button
          type="button"
          onClick={() => sessions.refetch()}
          className="mt-2 inline-flex min-h-[44px] items-center gap-2 rounded-md border px-3 text-xs font-medium"
        >
          <RefreshCw aria-hidden className="h-4 w-4" />
          Reintentar
        </button>
      </div>
    );
  }

  const rows = sessions.data ?? [];

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">{rows.length} sesiones activas</p>
        <button
          type="button"
          onClick={() => sessions.refetch()}
          className="inline-flex min-h-[44px] items-center gap-2 rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RefreshCw aria-hidden className="h-4 w-4" />
          Refrescar
        </button>
      </div>

      {rows.length === 0 ? (
        <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">
          No hay sesiones activas para mostrar.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border bg-card">
          <table className="w-full min-w-[880px] text-sm">
            <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">Usuario</th>
                <th className="px-4 py-2 font-medium">IP</th>
                <th className="px-4 py-2 font-medium">User agent</th>
                <th className="px-4 py-2 font-medium">Creada</th>
                <th className="px-4 py-2 font-medium">Última vista</th>
                <th className="px-4 py-2 font-medium">Token</th>
                <th className="px-4 py-2 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((session, index) => {
                const id = revocableId(session);
                return (
                  <tr key={sessionKey(session, index)} className="border-t">
                    <td className="px-4 py-3">{session.user_email || session.email || session.user_id || "—"}</td>
                    <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{session.ip || "—"}</td>
                    <td className="max-w-sm px-4 py-3 text-xs text-muted-foreground">
                      <span className="line-clamp-2">{session.user_agent || "—"}</span>
                    </td>
                    <td className="px-4 py-3 text-xs text-muted-foreground">{formatDate(session.created_at)}</td>
                    <td className="px-4 py-3 text-xs text-muted-foreground">{formatDate(session.last_seen)}</td>
                    <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{session.token_preview || "—"}</td>
                    <td className="px-4 py-3 text-right">
                      <button
                        type="button"
                        disabled={!id || revoke.isPending}
                        onClick={() => id && revoke.mutate(id)}
                        className="inline-flex min-h-[44px] items-center gap-2 rounded-md border border-destructive/40 px-3 text-xs font-medium text-destructive hover:bg-destructive/10 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40"
                      >
                        <ShieldOff aria-hidden className="h-4 w-4" />
                        Revocar
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {revoke.isError ? (
        <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          No se pudo revocar la sesión.
        </p>
      ) : null}
    </div>
  );
}
