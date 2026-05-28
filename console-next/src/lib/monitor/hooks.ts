"use client";

import { useQuery } from "@tanstack/react-query";

import {
  getFreshness,
  getJob,
  getJobLogs,
  getLineage,
  getPipeline,
  getSemantic,
  listJobs,
} from "./client";

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

export function useLineage(cartridge?: string) {
  return useQuery({
    queryKey: ["monitor", "lineage", cartridge ?? "all"],
    queryFn: () => getLineage(cartridge),
    refetchInterval: 60_000,
  });
}
