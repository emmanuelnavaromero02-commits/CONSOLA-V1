"use client";

import { FileText } from "lucide-react";
import { useState } from "react";

import { StatusPill } from "@/components/monitor/StatusPill";
import {
  findEntityRun,
  isTerminalRunStatus,
  useEntityRunLogs,
  useEntityRuns,
  useJob,
  useJobLogs,
} from "@/lib/monitor/hooks";
import { studioErrorMessage } from "@/lib/studio/client";

import { buttonClass, Notice, Spinner } from "./ui";

export interface ExtractionLaunch {
  entity: string;
  dagId: string | null;
  runId: string | null;
  jobId: string | null;
  launchedAt: number;
}

const FOLLOW_WINDOW_MS = 10 * 60_000;

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
  const runs = useEntityRuns(cartridge, launch.entity, launch.runId);
  const run = findEntityRun(runs.data, launch.runId);
  const terminal = isTerminalRunStatus(run?.status);
  const stalled = !terminal && runs.dataUpdatedAt - launch.launchedAt >= FOLLOW_WINDOW_MS - 5_000;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium">Corrida</span>
        <span className="break-all font-mono">{launch.runId}</span>
        {run ? <StatusPill status={run.status} /> : <span className="text-muted-foreground">En espera del registro de la corrida…</span>}
        {!terminal && !stalled ? <Spinner className="h-3 w-3" /> : null}
      </div>
      {run?.error ? <p className="break-words text-xs text-destructive">{run.error}</p> : null}
      {run?.record_count != null ? <p className="text-xs text-muted-foreground">{run.record_count} registros</p> : null}
      {stalled ? <p className="text-xs text-muted-foreground">Seguimiento detenido tras 10 minutos; revisa el monitor de pipelines.</p> : null}
      {runs.isError ? <p className="text-xs text-destructive">{studioErrorMessage(runs.error, "No se pudo consultar la corrida.")}</p> : null}
      <button type="button" className={buttonClass} onClick={() => setShowLogs((value) => !value)} aria-expanded={showLogs}>
        <FileText aria-hidden className="h-4 w-4" /> {showLogs ? "Ocultar logs" : "Ver logs"}
      </button>
      {showLogs && launch.runId ? <RunLogs cartridge={cartridge} entity={launch.entity} runId={launch.runId} /> : null}
    </div>
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
        {launch.dagId ? ` · DAG ${launch.dagId}` : ""}
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
