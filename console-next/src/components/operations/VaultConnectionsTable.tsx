"use client";

import { useState } from "react";

import { formatDistanceToNow } from "date-fns";
import { es } from "date-fns/locale";

import type { VaultConnection } from "@/lib/operations/types";
import { useVaultConnections } from "@/lib/operations/hooks";


const CARTRIDGES = [
  { id: "replicon",          label: "Replicon" },
  { id: "sap_hcm",           label: "SAP HCM" },
  { id: "sap_s4hana",        label: "SAP S/4HANA" },
  { id: "sap_successfactors", label: "SAP SuccessFactors" },
];


function relativeTime(value?: string | null): string {
  if (!value) return "—";
  const ts = Date.parse(value);
  if (!Number.isFinite(ts)) return "—";
  return formatDistanceToNow(new Date(ts), { addSuffix: true, locale: es });
}


/**
 * v1.44.4 Group 1 — Vault connections viewer.
 *
 * Read-only against /api/vault/connections/{cartridge}. Values
 * are NEVER fetched — only the connection metadata. The "Ver
 * secreto" reveal flow lives in the legacy console for now
 * (requires the ``vault.secrets.reveal`` permission, separate
 * audit log, and the v1.44.5 sprint to migrate cleanly).
 */
export function VaultConnectionsTable() {
  const [cartridge, setCartridge] = useState<string>("replicon");
  // Round 1 P1: use ``isFetching`` (true on first AND subsequent
  // refetches) instead of ``isLoading`` (first fetch only) so a
  // cartridge switch shows the skeleton during the new fetch
  // rather than leaving the previous cartridge's stale rows on
  // screen.
  const {
    data,
    isFetching,
    isError,
    refetch,
  } = useVaultConnections(cartridge);

  const connections: VaultConnection[] = data?.connections ?? [];

  return (
    <div className="space-y-4">
      <fieldset className="rounded-lg border bg-card p-4">
        <legend className="px-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Cartucho
        </legend>
        <div className="flex flex-wrap gap-2">
          {CARTRIDGES.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => setCartridge(c.id)}
              aria-pressed={cartridge === c.id}
              className={
                "inline-flex min-h-[44px] items-center justify-center rounded-md border px-4 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring " +
                (cartridge === c.id
                  ? "bg-primary text-primary-foreground"
                  : "bg-background hover:bg-accent/5")
              }
            >
              {c.label}
            </button>
          ))}
        </div>
      </fieldset>

      {isFetching && !isError ? (
        <div aria-busy="true" className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <span
              key={i}
              className="block h-14 animate-pulse rounded bg-muted"
              aria-hidden
            />
          ))}
        </div>
      ) : null}

      {isError ? (
        <div
          role="alert"
          className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm"
        >
          <p className="font-medium text-destructive">
            No se pudieron cargar las conexiones de Vault.
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

      {!isFetching && !isError && connections.length === 0 ? (
        <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">
          No hay conexiones guardadas para este cartucho.
        </p>
      ) : null}

      {!isFetching && !isError && connections.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border bg-card">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">ID</th>
                <th className="px-4 py-2 font-medium">Etiqueta</th>
                <th className="px-4 py-2 font-medium">Tipo</th>
                <th className="px-4 py-2 font-medium">Creado</th>
                <th className="px-4 py-2 font-medium">Última rotación</th>
                <th className="px-4 py-2 font-medium">Último uso</th>
              </tr>
            </thead>
            <tbody>
              {connections.map((c) => (
                <tr key={c.id} className="border-t">
                  <td className="px-4 py-3 font-mono text-xs">{c.id}</td>
                  <td className="px-4 py-3">{c.label ?? "—"}</td>
                  <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                    {c.kind ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {relativeTime(c.created_at)}
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {relativeTime(c.rotated_at)}
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {relativeTime(c.last_used_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <p className="text-xs text-muted-foreground">
        Para crear, rotar o revelar secretos, abre el módulo de Vault
        en la consola clásica.
      </p>
    </div>
  );
}
