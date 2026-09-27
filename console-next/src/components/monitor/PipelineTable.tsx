"use client";

import { AlertTriangle, Loader2, Play, PlayCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { api, isApiError } from "@/lib/api";
import {
  MAX_PROGRESS_RUNS,
  PROGRESS_FOLLOW_WINDOW_MS,
  findProgressRun,
  useExtractionProgress,
} from "@/lib/monitor/extraction-progress";
import { useVaultConnections } from "@/lib/monitor/hooks";
import type { ExtractAutomation, ExtractResult, PipelineEntity } from "@/lib/monitor/types";
import { pipelineErrorDetail, pipelineReasonCopy } from "@/lib/pipeline-error-copy";

import { ExtractionProgressCard } from "./ExtractionProgressCard";
import { StatusPill } from "./StatusPill";
import { StuckRunsDialog } from "./StuckRunsDialog";

const AGGREGATE_ENTITY = "__extract_all__";
const REUSED_NOTICE = "Ya había una extracción en curso; se muestra su avance.";

interface PipelineLaunch {
  runId: string;
  title: string;
  dagId: string | null;
  launchedAt: number;
  automationMessage: string | null;
  notice: string | null;
}

interface TriggeredItem {
  entity?: string | null;
  dag_id?: string | null;
  dag_run_id?: string | null;
  job_id?: string | null;
  reused?: boolean;
  result?: { automation?: ExtractAutomation | null } | null;
}

interface ExtractAllResult {
  count?: number;
  error_count?: number;
  reused?: boolean;
  automation?: ExtractAutomation | null;
  triggered?: TriggeredItem[];
  errors?: Array<{ entity?: string | null; error?: string | null; reason?: string | null }>;
}

function newLaunch(fields: Omit<PipelineLaunch, "launchedAt">): PipelineLaunch {
  return { ...fields, launchedAt: Date.now() };
}

function launchTitle(entity: string | null | undefined): string {
  return !entity || entity === AGGREGATE_ENTITY ? "Extracción completa" : `Extracción de ${entity}`;
}

function ExtractionLaunches({
  cartridge,
  launches,
  onOpenStuckRuns,
}: {
  cartridge: string;
  launches: PipelineLaunch[];
  onOpenStuckRuns: (dagId: string | null) => void;
}) {
  const newest = Math.max(...launches.map((launch) => launch.launchedAt));
  const progress = useExtractionProgress(
    cartridge,
    launches.map((launch) => launch.runId),
    newest,
  );
  const followStopped = progress.dataUpdatedAt - newest >= PROGRESS_FOLLOW_WINDOW_MS - 5_000;
  return (
    <div className="space-y-2 border-b px-3 py-3" data-testid="pipeline-extraction-launches">
      {launches.map((launch) => (
        <ExtractionProgressCard
          key={launch.runId}
          title={launch.title}
          runId={launch.runId}
          run={findProgressRun(progress.data, launch.runId)}
          loading={progress.isLoading}
          followStopped={followStopped}
          automationMessage={launch.automationMessage}
          notice={launch.notice}
          errorMessage={
            progress.isError
              ? progress.error instanceof Error
                ? progress.error.message
                : "No se pudo consultar el avance de la extracción."
              : null
          }
          onOpenStuckRuns={() => onOpenStuckRuns(launch.dagId)}
        />
      ))}
    </div>
  );
}

function countNodes(row: PipelineEntity): string {
  return `${row.silver.length} silver · ${row.gold.length} gold`;
}

function bronzeSummary(row: PipelineEntity): string {
  const date = row.bronze.latest_date || "sin fecha";
  if (row.bronze.empty) return `Sin filas extraídas · ${date}`;
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
  const [launches, setLaunches] = useState<PipelineLaunch[]>([]);
  const [stuckDagId, setStuckDagId] = useState<string | null | undefined>(undefined);
  const activeCartridge = cartridge || rows[0]?.cartridge || "";
  const connections = useVaultConnections(activeCartridge);
  const connectionOptions = (connections.data ?? [])
    .map((conn) => String(conn.conn_id || conn.id || "").trim())
    .filter(Boolean);
  const [selectedConnId, setSelectedConnId] = useState("");
  const effectiveConnId = selectedConnId || connectionOptions[0] || "";
  const isExtractAllLoading = extractingAll;

  function addLaunches(next: PipelineLaunch[]) {
    if (!next.length) return;
    setLaunches((current) => {
      const incoming = new Set(next.map((launch) => launch.runId));
      return [...next, ...current.filter((launch) => !incoming.has(launch.runId))].slice(0, MAX_PROGRESS_RUNS);
    });
  }

  function attachToRunningExtraction(error: unknown): boolean {
    const detail = isApiError(error) && error.status === 429 ? pipelineErrorDetail(error.data) : null;
    if (detail?.reason !== "extract_all_already_running" || !detail.jobId) return false;
    addLaunches([
      newLaunch({
        runId: detail.jobId,
        title: launchTitle(AGGREGATE_ENTITY),
        dagId: null,
        automationMessage: null,
        notice: detail.copy,
      }),
    ]);
    toast.info(detail.copy);
    return true;
  }

  async function extractEntity(row: PipelineEntity) {
    const key = `${row.cartridge}:${row.entity}`;
    setPendingEntity(key);
    try {
      const { data } = await api.post<ExtractResult>(
        `/api/pipeline/${encodeURIComponent(row.cartridge)}/${encodeURIComponent(row.entity)}/extract`,
        { mode: "incremental", ...(effectiveConnId ? { conn_id: effectiveConnId } : {}) },
      );
      const runId = data?.dag_run_id || data?.run_id || data?.job_id || null;
      if (runId) {
        addLaunches([
          newLaunch({
            runId,
            title: launchTitle(row.entity),
            dagId: data?.dag_id ?? null,
            automationMessage: data?.automation?.message_es ?? null,
            notice: data?.reused ? REUSED_NOTICE : null,
          }),
        ]);
      } else {
        toast.info(`Extracción enviada para ${row.entity}.`);
      }
      onExtractionStarted?.();
    } catch (error) {
      if (!attachToRunningExtraction(error)) {
        toast.error(error instanceof Error ? error.message : `No se pudo extraer ${row.entity}.`);
      }
    } finally {
      setPendingEntity(null);
    }
  }

  async function extractAll() {
    if (!activeCartridge) return;
    setExtractingAll(true);
    try {
      const { data } = await api.post<ExtractAllResult>(
        `/api/pipeline/${encodeURIComponent(activeCartridge)}/extract_all`,
        { mode: "incremental", ...(effectiveConnId ? { conn_id: effectiveConnId } : {}) },
      );
      const triggered = (data?.triggered ?? []).filter((item) => item.dag_run_id || item.job_id);
      addLaunches(
        triggered.slice(0, MAX_PROGRESS_RUNS).map((item) =>
          newLaunch({
            runId: String(item.dag_run_id || item.job_id),
            title: launchTitle(item.entity),
            dagId: item.dag_id ?? null,
            automationMessage: (data?.automation ?? item.result?.automation)?.message_es ?? null,
            notice: data?.reused || item.reused ? REUSED_NOTICE : null,
          }),
        ),
      );
      if (triggered.length > MAX_PROGRESS_RUNS) {
        toast.info(`Se siguen las primeras ${MAX_PROGRESS_RUNS} de ${triggered.length} extracciones enviadas.`);
      }
      const errors = data?.errors ?? [];
      if (errors.length) {
        const first = errors[0];
        const copy = pipelineReasonCopy(first?.reason) ?? first?.error ?? "No se pudo extraer.";
        toast.error(errors.length > 1 ? `${copy} (${errors.length} entidades con error)` : copy);
      } else if (!triggered.length) {
        toast.info("No se envió ninguna extracción.");
      }
      if (triggered.length) onExtractionStarted?.();
    } catch (error) {
      if (!attachToRunningExtraction(error)) {
        toast.error(error instanceof Error ? error.message : "No se pudo extraer todo.");
      }
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
        <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Extraer datos</span>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            onClick={() => setStuckDagId(null)}
            disabled={!activeCartridge}
            className="inline-flex min-h-[36px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
          >
            <AlertTriangle aria-hidden className="h-4 w-4" />
            Corridas atascadas
          </button>
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
      {launches.length && activeCartridge ? (
        <ExtractionLaunches
          cartridge={activeCartridge}
          launches={launches}
          onOpenStuckRuns={(dagId) => setStuckDagId(dagId)}
        />
      ) : null}
      <StuckRunsDialog
        open={stuckDagId !== undefined}
        cartridge={activeCartridge || null}
        dagId={stuckDagId ?? null}
        onClose={() => setStuckDagId(undefined)}
        onRecovered={() => onExtractionStarted?.()}
      />
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
