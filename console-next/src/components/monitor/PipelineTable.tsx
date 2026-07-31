"use client";

import { Loader2, Play, PlayCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import { useVaultConnections } from "@/lib/monitor/hooks";
import type { PipelineEntity } from "@/lib/monitor/types";
import { StatusPill } from "./StatusPill";

function countNodes(row: PipelineEntity): string {
  return `${row.silver.length} silver · ${row.gold.length} gold`;
}

function bronzeSummary(row: PipelineEntity): string {
  const date = row.bronze.latest_date || "sin fecha";
  if (row.bronze.empty) return `Sin filas extraídas · ${date}`;
  // record_count ausente ≠ 0 filas: sin conteo reportamos "N/D".
  if (row.bronze.record_count == null) return `N/D filas · ${date}`;
  return `${row.bronze.record_count} filas · ${date}`;
}

function downstreamSummary(row: PipelineEntity): string | null {
  const status = row.last_run?.silver_refresh_status?.trim();
  if (!status || ["success", "ok"].includes(status.toLowerCase())) return null;
  return `Silver: ${status}`;
}

export function PipelineTable({
  rows,
  cartridge,
  onExtractionStarted,
}: {
  rows: PipelineEntity[];
  cartridge?: string;
  onExtractionStarted?: () => void;
}) {
  const [pendingEntity, setPendingEntity] = useState<string | null>(null);
  const [extractingAll, setExtractingAll] = useState(false);
  const activeCartridge = cartridge || rows[0]?.cartridge || "";
  const connections = useVaultConnections(activeCartridge);
  const connectionOptions = (connections.data ?? [])
    .map((conn) => String(conn.conn_id || conn.id || "").trim())
    .filter(Boolean);
  const [selectedConnId, setSelectedConnId] = useState("");
  const effectiveConnId = selectedConnId || connectionOptions[0] || "";
  const isExtractAllLoading = extractingAll;

  async function extractEntity(row: PipelineEntity) {
    const key = `${row.cartridge}:${row.entity}`;
    setPendingEntity(key);
    try {
      await api.post(
        `/api/pipeline/${encodeURIComponent(row.cartridge)}/${encodeURIComponent(row.entity)}/extract`,
        { mode: "incremental", ...(effectiveConnId ? { conn_id: effectiveConnId } : {}) },
      );
      toast.success(`Extracción enviada para ${row.entity}.`);
      onExtractionStarted?.();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : `No se pudo extraer ${row.entity}.`);
    } finally {
      setPendingEntity(null);
    }
  }

  async function extractAll() {
    if (!activeCartridge) return;
    setExtractingAll(true);
    try {
      const { data } = await api.post<{ count?: number; error_count?: number }>(
        `/api/pipeline/${encodeURIComponent(activeCartridge)}/extract_all`,
        { mode: "incremental", ...(effectiveConnId ? { conn_id: effectiveConnId } : {}) },
      );
      const count = data.count ?? 0;
      const errors = data.error_count ?? 0;
      toast.success(
        errors
          ? `Extracción masiva enviada: ${count} OK, ${errors} con error.`
          : `Extracción masiva enviada: ${count} entidades.`,
      );
      onExtractionStarted?.();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "No se pudo extraer todo.");
    } finally {
      setExtractingAll(false);
    }
  }

  if (!rows.length) {
    return (
      <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">
        No hay entidades para este cartucho.
      </p>
    );
  }

  return (
    <div className="rounded-lg border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-3 py-2">
        <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Extracción</span>
        <div className="flex flex-wrap items-center justify-end gap-2">
          {connectionOptions.length ? (
            <label className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
              Conexión
              <select
                value={effectiveConnId}
                onChange={(event) => setSelectedConnId(event.target.value)}
                className="min-h-[36px] rounded-md border bg-background px-2 text-xs text-foreground"
              >
                {connectionOptions.map((connId) => (
                  <option key={connId} value={connId}>
                    {connId}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <button
            type="button"
            onClick={extractAll}
            disabled={isExtractAllLoading || !activeCartridge}
            className="inline-flex min-h-[36px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isExtractAllLoading ? (
              <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
            ) : (
              <PlayCircle aria-hidden className="h-4 w-4" />
            )}
            Extraer Todo
          </button>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-medium">Entidad</th>
              <th className="px-3 py-2 font-medium">Bronze</th>
              <th className="px-3 py-2 font-medium">Watermark</th>
              <th className="px-3 py-2 font-medium">Última corrida</th>
              <th className="px-3 py-2 font-medium">Capas</th>
              <th className="px-3 py-2 text-right font-medium">Acciones</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const key = `${row.cartridge}:${row.entity}`;
              const isRowLoading = pendingEntity === key;
              const downstream = downstreamSummary(row);
              return (
                <tr key={key} className="border-t">
                  <td className="px-3 py-2 align-top font-medium">{row.entity}</td>
                  <td className="px-3 py-2 align-top">
                    <div className="space-y-1">
                      <StatusPill status={row.bronze.status} />
                      <div className="text-xs text-muted-foreground">
                        {bronzeSummary(row)}
                      </div>
                    </div>
                  </td>
                  <td className="px-3 py-2 align-top font-mono text-xs text-muted-foreground">
                    {row.watermark || "-"}
                  </td>
                  <td className="px-3 py-2 align-top">
                    <div className="space-y-1">
                      <StatusPill status={row.last_run?.status || row.last_job?.status} />
                      <div className="text-xs text-muted-foreground">
                        {row.last_run?.finished_at || row.last_job?.finished_at || row.last_run?.started_at || "-"}
                      </div>
                      {downstream ? (
                        <div className="text-xs font-medium text-warning">{downstream}</div>
                      ) : null}
                    </div>
                  </td>
                  <td className="px-3 py-2 align-top text-xs text-muted-foreground">{countNodes(row)}</td>
                  <td className="px-3 py-2 text-right align-top">
                    <button
                      type="button"
                      onClick={() => extractEntity(row)}
                      disabled={isRowLoading || isExtractAllLoading}
                      className="inline-flex min-h-[36px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {isRowLoading ? (
                        <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
                      ) : (
                        <Play aria-hidden className="h-4 w-4" />
                      )}
                      Extraer
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
