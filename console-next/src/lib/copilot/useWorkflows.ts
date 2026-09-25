"use client";

import { useQuery } from "@tanstack/react-query";

import { listWorkflows, getWorkflow } from "./client";
import type {
  Workflow,
  WorkflowDetailResponse,
  WorkflowRunStatus,
} from "./types";

const TERMINAL: ReadonlyArray<WorkflowRunStatus> =
  ["completed", "cancelled", "failed"] as const;


export function useWorkflows() {
  return useQuery<Workflow[]>({
    queryKey: ["copilot", "workflows"],
    queryFn:  listWorkflows,
    staleTime: 30_000,
  });
}


export function useWorkflow(id: string | null) {
  return useQuery<WorkflowDetailResponse>({
    queryKey: ["copilot", "workflow", id],
    queryFn:  () => getWorkflow(id as string),
    enabled:  Boolean(id),
    refetchInterval: (q) => {
      const data = q.state.data as WorkflowDetailResponse | undefined;
      if (!data) return 5_000;
      return TERMINAL.includes(data.workflow.status) ? false : 5_000;
    },
  });
}
