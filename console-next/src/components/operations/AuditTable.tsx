"use client";

import { Fragment, useMemo, useState } from "react";

import { ChevronDown, ChevronRight } from "lucide-react";

import { formatDistanceToNow } from "date-fns";
import { es } from "date-fns/locale";

import type { AuditEvent } from "@/lib/operations/types";
import { useAuditEvents } from "@/lib/operations/hooks";


/**
 * v1.44.4 Group 1 — Audit log table.
 *
 * GET /security/audit returns up to 100 events (server-imposed
 * LIMIT). The table supports:
 *   - free-text filter across user_email / action /
 *     resource_type / resource_id / ip
 *   - per-row expandable detail panel showing the JSONB
 *     ``details`` payload
 *
 * Time format uses date-fns with es-MX locale so "hace 5 min"
 * reads naturally to Spanish operators.
 */
function relativeTime(value: string | null): string {
  if (!value) return "—";
  const ts = Date.parse(value);
  if (!Number.isFinite(ts)) return "—";
  return formatDistanceToNow(new Date(ts), { addSuffix: true, locale: es });
}


function eventMatches(e: AuditEvent, q: string): boolean {
  if (!q) return true;
  const needle = q.toLowerCase();
  return [
    e.user_email,
    e.action,
    e.resource_type,
    e.resource_id,
    e.ip,
  ].some((v) => (v ?? "").toLowerCase().includes(needle));
}


export function AuditTable() {
  const { data, isLoading, isError, refetch } = useAuditEvents();
  const [query,      setQuery]      = useState("");
  const [expanded,   setExpanded]   = useState<number | null>(null);

  const filtered = useMemo(() => {
    const events = data ?? [];
    return events.filter((e) => eventMatches(e, query));
  }, [data, query]);

  if (isLoading) {
    return (
      <div aria-busy="true" className="space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <span
            key={i}
            className="block h-12 animate-pulse rounded bg-muted"
            aria-hidden
          />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div
        role="alert"
        className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm"
      >
        <p className="font-medium text-destructive">
          No se pudo cargar la auditoría.
        </p>
        <button
          type="button"
          onClick={() => refetch()}
          className="mt-2 inline-flex min-h-[44px] items-center justify-center rounded-md border border-destructive/40 px-3 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40"
        >
          Reintentar
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <label className="flex flex-col gap-1.5 text-sm sm:flex-row sm:items-center sm:gap-2">
        <span className="font-medium">Filtrar</span>
        <input
          type="search"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setExpanded(null);
          }}
          placeholder="email, acción, recurso, IP…"
          className="min-h-[44px] w-full max-w-md rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <span className="text-xs text-muted-foreground">
          {filtered.length} de {data?.length ?? 0}
        </span>
      </label>

      {filtered.length === 0 ? (
        <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">
          {query
            ? "Sin coincidencias para esa búsqueda."
            : "No hay eventos de auditoría todavía."}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border bg-card">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="px-3 py-2 font-medium"></th>
                <th className="px-3 py-2 font-medium">Cuando</th>
                <th className="px-3 py-2 font-medium">Usuario</th>
                <th className="px-3 py-2 font-medium">Acción</th>
                <th className="px-3 py-2 font-medium">Recurso</th>
                <th className="px-3 py-2 font-medium">IP</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((e, idx) => {
                const key = e.id ?? -(idx + 1);
                const isOpen = expanded === key;
                const hasDetails = e.details
                  && typeof e.details === "object"
                  && Object.keys(e.details).length > 0;
                return (
                  <Fragment key={key}>
                    {/* Round 1 P1: button-only expand. Earlier
                        version made the whole row clickable but
                        had no keyboard equivalent — dropped the
                        onClick so the chevron button is the sole
                        affordance, fully keyboard-reachable. */}
                    <tr className="border-t">
                      <td className="px-3 py-2 align-top">
                        {hasDetails ? (
                          <button
                            type="button"
                            aria-label={isOpen ? "Ocultar detalles" : "Ver detalles"}
                            aria-expanded={isOpen}
                            onClick={() => setExpanded(isOpen ? null : key)}
                            className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded text-muted-foreground hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          >
                            {isOpen ? (
                              <ChevronDown aria-hidden className="h-4 w-4" />
                            ) : (
                              <ChevronRight aria-hidden className="h-4 w-4" />
                            )}
                          </button>
                        ) : null}
                      </td>
                      <td className="px-3 py-2 align-top text-xs text-muted-foreground">
                        {relativeTime(e.created_at)}
                      </td>
                      <td className="px-3 py-2 align-top">
                        {e.user_email ?? "—"}
                      </td>
                      <td className="px-3 py-2 align-top font-mono text-xs">
                        {e.action ?? "—"}
                      </td>
                      <td className="px-3 py-2 align-top text-xs">
                        {e.resource_type ? (
                          <>
                            <span className="text-muted-foreground">{e.resource_type}</span>
                            {e.resource_id ? (
                              <>
                                <span aria-hidden> · </span>
                                <span className="font-mono">{e.resource_id}</span>
                              </>
                            ) : null}
                          </>
                        ) : "—"}
                      </td>
                      <td className="px-3 py-2 align-top font-mono text-xs text-muted-foreground">
                        {e.ip ?? "—"}
                      </td>
                    </tr>
                    {isOpen && hasDetails ? (
                      <tr className="border-t bg-muted/20">
                        <td colSpan={6} className="px-3 py-3">
                          <pre
                            aria-label="Detalles del evento"
                            tabIndex={0}
                            className="max-h-72 overflow-auto rounded bg-background p-3 text-[11px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          >
{JSON.stringify(e.details, null, 2)}
                          </pre>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
