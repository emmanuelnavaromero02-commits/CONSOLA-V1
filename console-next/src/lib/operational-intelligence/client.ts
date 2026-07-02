import { api } from "@/lib/api";

import type {
  ConfidenceSummary,
  DecisionPlanRequest,
  DecisionPlanSummary,
  HistoricalValidationRequest,
  HistoricalValidationSummary,
  OperationalList,
  OperationalRecord,
  OperationalRunSummary,
  ScenarioSummary,
} from "./types";

function asItems<T extends OperationalRecord>(value: unknown, keys: string[]): T[] {
  if (Array.isArray(value)) return value as T[];
  if (!value || typeof value !== "object") return [];
  const record = value as Record<string, unknown>;
  for (const key of keys) {
    const rows = record[key];
    if (Array.isArray(rows)) return rows as T[];
  }
  return [];
}

export async function listScenarioAnalyses(limit = 50): Promise<OperationalList<ScenarioSummary>> {
  const { data } = await api.get<unknown>(
    `/api/intelligence/monte-carlo?limit=${encodeURIComponent(String(limit))}`,
  );
  return {
    ...(data && typeof data === "object" ? data as OperationalRecord : {}),
    items: asItems<ScenarioSummary>(data, ["simulations", "items", "rows", "data"]),
  };
}

export async function listDecisionPlans(limit = 50): Promise<OperationalList<DecisionPlanSummary>> {
  const { data } = await api.get<unknown>(
    `/api/intelligence/orchestrate?limit=${encodeURIComponent(String(limit))}`,
  );
  return {
    ...(data && typeof data === "object" ? data as OperationalRecord : {}),
    items: asItems<DecisionPlanSummary>(data, ["orchestrations", "plans", "items", "rows", "data"]),
  };
}

export async function createDecisionPlan(
  request: DecisionPlanRequest,
): Promise<DecisionPlanSummary> {
  const { data } = await api.post<DecisionPlanSummary>("/api/intelligence/orchestrate", request);
  return data;
}

export async function listOperationalRuns(limit = 50): Promise<OperationalList<OperationalRunSummary>> {
  const { data } = await api.get<unknown>(
    `/api/intelligence/runs?limit=${encodeURIComponent(String(limit))}`,
  );
  return {
    ...(data && typeof data === "object" ? data as OperationalRecord : {}),
    items: asItems<OperationalRunSummary>(data, ["runs", "items", "rows", "data"]),
  };
}

export async function listOperationalHistory(limit = 100): Promise<OperationalList<OperationalRecord>> {
  const { data } = await api.get<unknown>(
    `/api/intelligence/history?limit=${encodeURIComponent(String(limit))}`,
  );
  return {
    ...(data && typeof data === "object" ? data as OperationalRecord : {}),
    items: asItems<OperationalRecord>(data, ["history", "items", "rows", "data"]),
  };
}

export async function getConfidenceHistory(): Promise<ConfidenceSummary> {
  const { data } = await api.get<ConfidenceSummary>("/api/intelligence/calibration");
  return data;
}

export async function listHistoricalValidations(
  limit = 50,
): Promise<OperationalList<HistoricalValidationSummary>> {
  const { data } = await api.get<unknown>(
    `/api/intelligence/backtests?limit=${encodeURIComponent(String(limit))}`,
  );
  return {
    ...(data && typeof data === "object" ? data as OperationalRecord : {}),
    items: asItems<HistoricalValidationSummary>(data, ["backtests", "validations", "items", "rows", "data"]),
  };
}

export async function runHistoricalValidation(
  request: HistoricalValidationRequest,
): Promise<HistoricalValidationSummary> {
  const { data } = await api.post<HistoricalValidationSummary>("/api/intelligence/backtests/run", request);
  return data;
}
