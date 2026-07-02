import { api } from "@/lib/api";

import type {
  ActionMutationRequest,
  DryRunActionRequest,
  SupervisedAction,
  SupervisedActionList,
} from "./types";

function normalizeActions(data: unknown): SupervisedAction[] {
  if (Array.isArray(data)) return data as SupervisedAction[];
  if (!data || typeof data !== "object") return [];
  const record = data as Record<string, unknown>;
  const actions = record.actions ?? record.items ?? record.rows ?? record.data;
  return Array.isArray(actions) ? actions as SupervisedAction[] : [];
}

export async function listSupervisedActions(limit = 50): Promise<SupervisedActionList> {
  const { data } = await api.get<unknown>(
    `/api/actions?limit=${encodeURIComponent(String(limit))}`,
  );
  return {
    ...(data && typeof data === "object" ? data as Record<string, unknown> : {}),
    actions: normalizeActions(data),
  };
}

export async function getSupervisedAction(actionId: string): Promise<SupervisedAction> {
  const { data } = await api.get<SupervisedAction>(
    `/api/actions/${encodeURIComponent(actionId)}`,
  );
  return data;
}

export async function validateSupervisedAction(
  actionId: string,
  request: DryRunActionRequest = {},
): Promise<SupervisedAction> {
  const { data } = await api.post<SupervisedAction>(
    `/api/actions/${encodeURIComponent(actionId)}/dry-run`,
    request,
  );
  return data;
}

export async function approveSupervisedAction(
  actionId: string,
  request: ActionMutationRequest = {},
): Promise<SupervisedAction> {
  const { data } = await api.post<SupervisedAction>(
    `/api/actions/${encodeURIComponent(actionId)}/approve`,
    request,
  );
  return data;
}

export async function rejectSupervisedAction(
  actionId: string,
  request: ActionMutationRequest = {},
): Promise<SupervisedAction> {
  const { data } = await api.post<SupervisedAction>(
    `/api/actions/${encodeURIComponent(actionId)}/reject`,
    request,
  );
  return data;
}

export async function executeSupervisedAction(
  actionId: string,
  request: ActionMutationRequest = {},
): Promise<SupervisedAction> {
  const { data } = await api.post<SupervisedAction>(
    `/api/actions/${encodeURIComponent(actionId)}/execute`,
    request,
  );
  return data;
}

export async function cancelSupervisedAction(
  actionId: string,
  request: ActionMutationRequest = {},
): Promise<SupervisedAction> {
  const { data } = await api.post<SupervisedAction>(
    `/api/actions/${encodeURIComponent(actionId)}/cancel`,
    request,
  );
  return data;
}
