"use client";

import { useQuery } from "@tanstack/react-query";
import { z } from "zod";

import { api } from "@/lib/api";

export const EXTRACTION_PHASES = ["connecting", "extracting", "building", "ready"] as const;
export type ExtractionPhase = (typeof EXTRACTION_PHASES)[number];

export const EXTRACTION_STEP_LABELS: Record<ExtractionPhase, string> = {
  connecting: "Conexión",
  extracting: "Extracción",
  building: "Vistas de negocio",
  ready: "Listo",
};

export const PROGRESS_POLL_MS = 2_500;
export const PROGRESS_FOLLOW_WINDOW_MS = 30 * 60_000;
export const MAX_PROGRESS_RUNS = 20;

const count = z.number().int().nonnegative().nullable();

export const extractionProgressRunSchema = z
  .object({
    run_id: z.string(),
    entity: z.string(),
    phase: z.enum(EXTRACTION_PHASES),
    phase_index: z.number().int().min(1).max(4),
    status: z.string(),
    terminal: z.boolean(),
    outcome: z.enum(["success", "partial", "failed"]).nullable(),
    record_count: count,
    entities_done: count,
    entities_total: count,
    error: z.string().nullable(),
    recovered: z.boolean(),
    stalled: z.boolean(),
    started_at: z.string().nullable(),
    finished_at: z.string().nullable(),
  })
  .strict();

export const extractionProgressSchema = z
  .object({
    schema_version: z.literal("pipeline-extraction-progress/v1"),
    checked_at: z.string(),
    runs: z.array(extractionProgressRunSchema).max(MAX_PROGRESS_RUNS),
  })
  .strict();

export type ExtractionProgressRun = z.infer<typeof extractionProgressRunSchema>;
export type ExtractionProgress = z.infer<typeof extractionProgressSchema>;

const recoveryClassification = z.enum([
  "not_applicable",
  "too_recent",
  "live",
  "waiting_turn",
  "airflow_terminal",
  "missing_in_airflow",
  "stalled_queued_paused_dag",
  "stalled_queued_no_progress",
  "stalled_running_no_tasks",
  "unverifiable",
  "airflow_orphan",
]);

export const stuckRunRecoverySchema = z
  .object({
    schema_version: z.literal("pipeline-recovery/v1"),
    mode: z.enum(["dry_run", "applied"]),
    checked_at: z.string(),
    threshold_minutes: z.number().int().positive(),
    plan_digest: z.string().regex(/^[a-f0-9]{64}$/),
    counts: z
      .object({
        candidates: z.number().int().nonnegative(),
        recoverable: z.number().int().nonnegative(),
        recovered: z.number().int().nonnegative(),
        synced_terminal: z.number().int().nonnegative(),
        live: z.number().int().nonnegative(),
        unverifiable: z.number().int().nonnegative(),
        not_applicable: z.number().int().nonnegative(),
        conflicts: z.number().int().nonnegative(),
        airflow_neutralized: z.number().int().nonnegative(),
        airflow_neutralize_failed: z.number().int().nonnegative(),
      })
      .strict(),
    runs: z
      .array(
        z
          .object({
            run_id: z.string(),
            dag_id: z.string(),
            cartridge: z.string(),
            entity: z.string(),
            status_before: z.string(),
            status_after: z.string().nullable(),
            started_at: z.string().nullable(),
            age_minutes: z.number().int().nonnegative().nullable(),
            created_today: z.boolean(),
            classification: recoveryClassification,
            action: z.enum(["mark_failed", "sync_terminal", "neutralize_airflow", "none"]),
            neutralize_airflow: z.boolean(),
            reason_es: z.string(),
          })
          .strict(),
      )
      .max(200),
    truncated: z.boolean(),
    message_es: z.string(),
  })
  .strict();

export type StuckRunRecovery = z.infer<typeof stuckRunRecoverySchema>;

export interface StuckRunRecoveryInput {
  apply?: boolean;
  plan_digest?: string;
  cartridge?: string;
  dag_id?: string;
}

function uniqueRunIds(runIds: Array<string | null | undefined>): string[] {
  return [...new Set(runIds.map((id) => String(id ?? "").trim()).filter(Boolean))].slice(0, MAX_PROGRESS_RUNS);
}

export async function getExtractionProgress(cartridge: string, runIds: string[]): Promise<ExtractionProgress> {
  const params = new URLSearchParams({ cartridge });
  for (const runId of uniqueRunIds(runIds)) params.append("run_id", runId);
  const { data } = await api.get<unknown>(`/api/pipelines/extraction-progress?${params.toString()}`);
  return extractionProgressSchema.parse(data);
}

export async function recoverStuckRuns(input: StuckRunRecoveryInput = {}): Promise<StuckRunRecovery> {
  const body: Record<string, unknown> = {};
  if (input.apply) {
    body.apply = true;
    body.plan_digest = input.plan_digest;
  }
  if (input.cartridge) body.cartridge = input.cartridge;
  if (input.dag_id) body.dag_id = input.dag_id;
  const { data } = await api.post<unknown>("/api/pipelines/recover-stuck-runs", body);
  return stuckRunRecoverySchema.parse(data);
}

export function findProgressRun(
  progress: ExtractionProgress | undefined,
  runId: string | null | undefined,
): ExtractionProgressRun | undefined {
  if (!progress || !runId) return undefined;
  return progress.runs.find((run) => run.run_id === runId);
}

export function allRunsTerminal(progress: ExtractionProgress | undefined, runIds: string[]): boolean {
  const ids = uniqueRunIds(runIds);
  if (!progress || !ids.length) return false;
  return ids.every((id) => findProgressRun(progress, id)?.terminal === true);
}

function formatCount(value: number): string {
  return new Intl.NumberFormat("es-MX").format(value);
}

export interface PhaseCopy {
  title: string;
  detail: string | null;
  tone: "progress" | "success" | "warning" | "error";
}

export function phaseCopy(run: ExtractionProgressRun | undefined): PhaseCopy {
  if (!run) {
    return { title: "Esperando el registro de la corrida…", detail: null, tone: "progress" };
  }
  if (run.outcome === "failed") {
    return { title: "No se pudo completar", detail: run.error, tone: "error" };
  }
  if (run.stalled) {
    return {
      title: "La ejecución no avanza",
      detail: "La corrida sigue en cola más tiempo del esperado. Revisa las corridas atascadas.",
      tone: "warning",
    };
  }
  if (run.phase === "connecting") {
    return { title: "Conectando de forma segura con el origen...", detail: null, tone: "progress" };
  }
  if (run.phase === "extracting") {
    const records =
      run.record_count != null
        ? ` (${formatCount(run.record_count)} ${run.record_count === 1 ? "registro descargado" : "registros descargados"})`
        : "";
    let detail: string | null = null;
    if (run.entities_done != null) {
      detail = `${formatCount(run.entities_done)} ${run.entities_done === 1 ? "entidad completada" : "entidades completadas"}`;
      if (run.entities_total != null) detail += ` de ${formatCount(run.entities_total)}`;
    }
    return { title: `Extrayendo entidades seleccionadas...${records}`, detail, tone: "progress" };
  }
  if (run.phase === "building") {
    return { title: "Construyendo vistas de negocio y actualizando catálogo...", detail: null, tone: "progress" };
  }
  if (run.outcome === "partial") {
    return { title: "Completado con advertencias", detail: run.error, tone: "warning" };
  }
  return { title: "Listo para su análisis.", detail: null, tone: "success" };
}

export function useExtractionProgress(
  cartridge: string | null | undefined,
  runIds: Array<string | null | undefined>,
  startedAt: number,
) {
  const ids = uniqueRunIds(runIds);
  return useQuery({
    queryKey: ["monitor", "extraction-progress", cartridge ?? "", ids],
    queryFn: () => getExtractionProgress(cartridge as string, ids),
    enabled: Boolean(cartridge && ids.length),
    refetchInterval: (query) => {
      if (Date.now() - startedAt >= PROGRESS_FOLLOW_WINDOW_MS) return false;
      return allRunsTerminal(query.state.data, ids) ? false : PROGRESS_POLL_MS;
    },
    refetchIntervalInBackground: false,
  });
}
