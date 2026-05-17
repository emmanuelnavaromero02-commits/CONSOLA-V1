"use client";

import { useQuery } from "@tanstack/react-query";

import { listWorkflows, getWorkflow } from "./client";
import type {
  Workflow,
  WorkflowDetailResponse,
  WorkflowRunStatus,
} from "./types";

/**
 * v1.44.4 Task A — workflows hook.
 *
 * Backend reality: GET /api/copilot/workflow/{id} returns an
 * envelope ``{workflow, steps}`` (NOT a flat Workflow). The
 * detail hook preserves both so the visualiser can iterate
 * steps without a separate fetch.
 *
 * SSE for live step updates is documented as next-session
 * work (copilot_workflows.py:6-7); we poll every 5 s while
 * non-terminal and stop once status is completed / cancelled /
 * failed.
 */
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
