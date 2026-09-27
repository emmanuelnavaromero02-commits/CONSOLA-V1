"use client";

import { FileText } from "lucide-react";
import { useState } from "react";

import { ExtractionProgressCard } from "@/components/monitor/ExtractionProgressCard";
import { StatusPill } from "@/components/monitor/StatusPill";
import { StuckRunsDialog } from "@/components/monitor/StuckRunsDialog";
import { isApiError } from "@/lib/api";
import { extractEntity } from "@/lib/monitor/client";
import {
  PROGRESS_FOLLOW_WINDOW_MS,
  findProgressRun,
  useExtractionProgress,
} from "@/lib/monitor/extraction-progress";
import { useEntityRunLogs, useJob, useJobLogs } from "@/lib/monitor/hooks";
import { pipelineErrorDetail } from "@/lib/pipeline-error-copy";
import { studioErrorMessage } from "@/lib/studio/client";

import { buttonClass, Notice, Spinner } from "./ui";

export interface ExtractionLaunch {
  entity: string;
  dagId: string | null;
  runId: string | null;
  jobId: string | null;
  launchedAt: number;
  automationMessage?: string | null;
  attachedToRunning?: boolean;
}

export type ExtractionMode = "full" | "incremental";

export function extractionMode(mode: string | null | undefined): ExtractionMode {
  return String(mode || "incremental") === "full" ? "full" : "incremental";
}

export async function startExtraction(cartridge: string, entity: string, mode: ExtractionMode): Promise<ExtractionLaunch> {
  try {
    const result = await extractEntity(cartridge, entity, { mode });
    const runId = result.dag_run_id || null;
    return {
      entity,
      dagId: result.dag_id || null,
      runId,
      jobId: runId ? null : result.job_id || null,
      launchedAt: Date.now(),
      automationMessage: result.automation?.message_es ?? null,
      attachedToRunning: Boolean(result.reused),
    };
  } catch (error) {
    const detail = isApiError(error) && error.status === 429 ? pipelineErrorDetail(error.data) : null;
    if (detail?.reason === "extract_all_already_running" && detail.jobId) {
      return {
        entity,
        dagId: null,
        runId: detail.jobId,
        jobId: null,
        launchedAt: Date.now(),
        attachedToRunning: true,
      };
    }
    throw error;
  }
}

export function extractionLaunchNotice(launch: ExtractionLaunch): string {
  return launch.attachedToRunning
    ? "Ya había una extracción en curso; se muestra su avance."
    : `Extracción enviada para ${launch.entity}.`;
}

function RunLogs({ cartridge, entity, runId }: { cartridge: string; entity: string; runId: string }) {
  const logs = useEntityRunLogs(cartridge, entity, runId, true);
  if (logs.isLoading) {
    return <p className="flex items-center gap-2 text-xs text-muted-foreground"><Spinner /> Cargando logs de Airflow…</p>;
  }
  if (logs.isError) return <Notice tone="error">{studioErrorMessage(logs.error, "No se pudieron leer los logs.")}</Notice>;
  const tasks = logs.data?.logs ?? [];
  if (!tasks.length) {
    return <Notice tone="warning">{logs.data?.error || "Airflow no devolvió logs para esta corrida."}</Notice>;
  }
  return (
    <div className="space-y-2">
      {tasks.map((task, index) => (
        <div key={`${task.task_id ?? "task"}-${index}`}>
          <p className="text-xs font-medium">Tarea {task.task_id || "—"}</p>
          {task.available && task.logs ? (
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md border bg-background p-2 font-mono text-[11px]">
              {task.logs.slice(-12_000)}
            </pre>
          ) : (
            <p className="text-xs text-muted-foreground">{task.error || "Sin logs disponibles."}</p>
          )}
        </div>
      ))}
    </div>
  );
}

function DagRunTracker({ cartridge, launch }: { cartridge: string; launch: ExtractionLaunch }) {
  const [showLogs, setShowLogs] = useState(false);
  const [stuckOpen, setStuckOpen] = useState(false);
  const progress = useExtractionProgress(cartridge, [launch.runId], launch.launchedAt);
  const run = findProgressRun(progress.data, launch.runId);
  const followStopped = progress.dataUpdatedAt - launch.launchedAt >= PROGRESS_FOLLOW_WINDOW_MS - 5_000;
  return (
    <>
      <ExtractionProgressCard
        title={`Extracción de ${launch.entity}`}
        runId={launch.runId}
        run={run}
        loading={progress.isLoading}
        followStopped={followStopped}
        automationMessage={launch.automationMessage ?? null}
        notice={launch.attachedToRunning ? "Ya había una extracción en curso; se muestra su avance." : null}
        errorMessage={progress.isError ? studioErrorMessage(progress.error, "No se pudo consultar el avance de la corrida.") : null}
        onOpenStuckRuns={() => setStuckOpen(true)}
        details={
          <>
            <button type="button" className={buttonClass} onClick={() => setShowLogs((value) => !value)} aria-expanded={showLogs}>
              <FileText aria-hidden className="h-4 w-4" /> {showLogs ? "Ocultar logs" : "Ver logs"}
            </button>
            {showLogs && launch.runId ? <RunLogs cartridge={cartridge} entity={launch.entity} runId={launch.runId} /> : null}
          </>
        }
      />
      <StuckRunsDialog
        open={stuckOpen}
        cartridge={cartridge}
        dagId={launch.dagId}
        onClose={() => setStuckOpen(false)}
        onRecovered={() => void progress.refetch()}
      />
    </>
  );
}

function JobTracker({ jobId }: { jobId: string }) {
  const job = useJob(jobId);
  const logs = useJobLogs(jobId);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium">Job</span>
        <span className="break-all font-mono">{jobId}</span>
        {job.data ? <StatusPill status={job.data.status} /> : <Spinner className="h-3 w-3" />}
      </div>
      {job.data?.message ? <p className="text-xs text-muted-foreground">{job.data.message}</p> : null}
      {logs.data?.length ? (
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md border bg-background p-2 font-mono text-[11px]">
          {logs.data.map((line) => `${line.ts ?? ""} ${line.level ?? ""} ${line.message ?? ""}`.trim()).join("\n")}
        </pre>
      ) : null}
    </div>
  );
}

export function ExtractionTracker({ cartridge, launch }: { cartridge: string; launch: ExtractionLaunch }) {
  return (
    <div data-testid="extraction-tracker" className="rounded-md border bg-muted/20 p-3">
      <p className="mb-2 text-xs font-semibold uppercase text-muted-foreground">
        Extracción de {launch.entity}
        {launch.dagId ? ` · automatización ${launch.dagId}` : ""}
      </p>
      {launch.runId ? (
        <DagRunTracker cartridge={cartridge} launch={launch} />
      ) : launch.jobId ? (
        <JobTracker jobId={launch.jobId} />
      ) : (
        <p className="text-xs text-muted-foreground">La extracción se envió, pero el backend no devolvió un identificador de corrida para seguirla.</p>
      )}
    </div>
  );
}
