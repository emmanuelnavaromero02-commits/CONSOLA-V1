"use client";

import { useQuery } from "@tanstack/react-query";

import { listWorkflows, getWorkflow } from "./client";
import type { Workflow } from "./types";

/**
 * v1.44.4 Task A — workflows hook.
 *
 * The WorkflowVisualizer surfaces multi-step plans the copilot
 * builds via /api/copilot/workflow. SSE streaming for live
 * step-status updates is documented as next-session work
 * (copilot_workflows.py:6-7); for now we poll while a workflow
 * is non-terminal.
 */
const TERMINAL: ReadonlyArray<Workflow["status"]> =
  ["completed", "cancelled", "failed"] as const;


export function useWorkflows() {
  return useQuery<Workflow[]>({
    queryKey: ["copilot", "workflows"],
    queryFn:  listWorkflows,
    staleTime: 30_000,
  });
}


export function useWorkflow(id: string | null) {
  return useQuery<Workflow>({
    queryKey: ["copilot", "workflow", id],
    queryFn:  () => getWorkflow(id as string),
    enabled:  Boolean(id),
    // Poll every 5 s while non-terminal so the visualiser
    // reflects backend step transitions without a manual
    // refresh.
    refetchInterval: (q) => {
      const data = q.state.data as Workflow | undefined;
      if (!data) return 5_000;
      return TERMINAL.includes(data.status) ? false : 5_000;
    },
  });
}
