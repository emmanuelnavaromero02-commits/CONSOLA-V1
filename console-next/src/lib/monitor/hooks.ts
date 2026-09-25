"use client";

import { useQuery } from "@tanstack/react-query";

import {
  getDatasetDetail,
  getEntityRunLogs,
  getDatasetLineage,
  getDatasetPreview,
  getFreshness,
  getJob,
  getJobLogs,
  getLineage,
  getPipeline,
  getSemantic,
  getSourceSchema,
  listDatasets,
  listEntityRuns,
  listJobs,
  listSources,
  listVaultConnections,
  listVaultSecrets,
} from "./client";
import type { EntityRun } from "./types";

const TERMINAL_RUN_STATUSES = new Set([
  "success",
  "partial",
  "failed",
  "error",
  "done",
  "skipped",
  "upstream_failed",
  "cancelled",
]);
const MAX_RUN_POLLS = 120;

export function isTerminalRunStatus(status?: string | null): boolean {
  return TERMINAL_RUN_STATUSES.has(String(status ?? "").toLowerCase());
}

export function findEntityRun(runs: EntityRun[] | undefined, runId: string | null): EntityRun | undefined {
  if (!runs?.length || !runId) return undefined;
  return runs.find((run) => run.dag_run_id === runId || run.run_id === runId);
}

export function useJobs(limit = 100) {
  return useQuery({
    queryKey: ["monitor", "jobs", limit],
    queryFn: () => listJobs(limit),
    refetchInterval: 20_000,
  });
}

export function useJob(jobId: string | null) {
  return useQuery({
    queryKey: ["monitor", "job", jobId],
    queryFn: () => getJob(jobId as string),
    enabled: Boolean(jobId),
    refetchInterval: 10_000,
  });
}

export function useJobLogs(jobId: string | null) {
  return useQuery({
    queryKey: ["monitor", "job", jobId, "logs"],
    queryFn: () => getJobLogs(jobId as string),
    enabled: Boolean(jobId),
    refetchInterval: 10_000,
  });
}

export function usePipeline(cartridge: string) {
  return useQuery({
    queryKey: ["monitor", "pipeline", cartridge],
    queryFn: () => getPipeline(cartridge),
    refetchInterval: 30_000,
  });
}

export function useFreshness(cartridge: string) {
  return useQuery({
    queryKey: ["monitor", "freshness", cartridge],
    queryFn: () => getFreshness(cartridge),
    refetchInterval: 30_000,
  });
}

export function useSemantic(cartridge: string) {
  return useQuery({
    queryKey: ["monitor", "semantic", cartridge],
    queryFn: () => getSemantic(cartridge),
    staleTime: 60_000,
  });
}

export function useDatasets() {
  return useQuery({
    queryKey: ["monitor", "datasets"],
    queryFn: () => listDatasets(),
    staleTime: 30_000,
  });
}

export function useDatasetDetail(name: string | null) {
  return useQuery({
    queryKey: ["monitor", "dataset", name, "detail"],
    queryFn: () => getDatasetDetail(name as string),
    enabled: Boolean(name),
    staleTime: 30_000,
  });
}

export function useDatasetPreview(name: string | null, limit = 20) {
  return useQuery({
    queryKey: ["monitor", "dataset", name, "preview", limit],
    queryFn: () => getDatasetPreview(name as string, limit),
    enabled: Boolean(name),
    staleTime: 30_000,
  });
}

export function useDatasetLineage(name: string | null) {
  return useQuery({
    queryKey: ["monitor", "dataset", name, "lineage"],
    queryFn: () => getDatasetLineage(name as string),
    enabled: Boolean(name),
    staleTime: 30_000,
  });
}

export function useSources() {
  return useQuery({
    queryKey: ["monitor", "sources"],
    queryFn: () => listSources(),
    staleTime: 60_000,
  });
}

export function useSourceSchema(source: string | null) {
  return useQuery({
    queryKey: ["monitor", "source-schema", source],
    queryFn: () => getSourceSchema(source as string),
    enabled: Boolean(source),
    staleTime: 30_000,
  });
}

export function useLineage(cartridge?: string) {
  return useQuery({
    queryKey: ["monitor", "lineage", cartridge || "all"],
    queryFn: () => getLineage(cartridge || undefined),
    staleTime: 30_000,
  });
}

export function useVaultConnections(cartridge: string) {
  return useQuery({
    queryKey: ["monitor", "vault", "connections", cartridge],
    queryFn: () => listVaultConnections(cartridge),
    enabled: Boolean(cartridge),
    staleTime: 30_000,
  });
}

export function useVaultSecrets(scope: string) {
  return useQuery({
    queryKey: ["monitor", "vault", "secrets", scope],
    queryFn: () => listVaultSecrets(scope),
    staleTime: 30_000,
  });
}

export function useEntityRuns(cartridge: string | null, entity: string | null, runId: string | null) {
  return useQuery({
    queryKey: ["monitor", "entity-runs", cartridge, entity, runId],
    queryFn: () => listEntityRuns(cartridge as string, entity as string, 10),
    enabled: Boolean(cartridge && entity && runId),
    refetchInterval: (query) => {
      if (query.state.dataUpdateCount >= MAX_RUN_POLLS) return false;
      const run = findEntityRun(query.state.data, runId);
      return run && isTerminalRunStatus(run.status) ? false : 5_000;
    },
  });
}

export function useEntityRunLogs(
  cartridge: string | null,
  entity: string | null,
  dagRunId: string | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ["monitor", "entity-run-logs", cartridge, entity, dagRunId],
    queryFn: () => getEntityRunLogs(cartridge as string, entity as string, dagRunId as string),
    enabled: Boolean(enabled && cartridge && entity && dagRunId),
    staleTime: 10_000,
  });
}
