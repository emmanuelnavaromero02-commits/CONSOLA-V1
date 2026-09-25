import { api, isApiError } from "@/lib/api";

import type {
  SapB1BusinessParameters,
  SapB1FinanceRunResult,
  SapB1Indicators,
  SapB1LoadRow,
  SapB1Mapping,
  SapB1Overview,
  SapB1Recipients,
  SapB1SaveParametersResult,
  SapB1View,
  SapB1ViewName,
} from "./types";

export const SAP_B1_CARTRIDGE = "sap_b1";
export const LOAD_RECONCILIATION_DATASET = "sap_b1_load_reconciliation";

export const SAP_B1_PATHS = {
  overview: "/api/sap-b1/overview",
  mapping: "/api/sap-b1/mapping",
  indicators: "/api/sap-b1/indicators",
  businessParameters: "/api/sap-b1/business-parameters",
  financeRuns: "/api/sap-b1/finance-runs",
  recipients: "/api/sap-b1/recipients",
} as const;

export function sapB1ViewPath(view: SapB1ViewName, topN = 0): string {
  const query = topN > 0 ? `?top_n=${Math.min(10, Math.floor(topN))}` : "";
  return `/api/control-room/sap-b1/views/${encodeURIComponent(view)}${query}`;
}

export function recipientPath(email: string): string {
  return `${SAP_B1_PATHS.recipients}/${encodeURIComponent(email)}`;
}

export function loadReconciliationPath(limit = 5000): string {
  return `/api/data/${LOAD_RECONCILIATION_DATASET}?limit=${limit}`;
}

export async function getSapB1Overview(): Promise<SapB1Overview> {
  const { data } = await api.get<SapB1Overview>(SAP_B1_PATHS.overview);
  return data;
}

export async function getSapB1Mapping(): Promise<SapB1Mapping> {
  const { data } = await api.get<Partial<SapB1Mapping>>(SAP_B1_PATHS.mapping);
  return { entities: Array.isArray(data?.entities) ? data.entities : [] };
}

export async function getSapB1Indicators(): Promise<SapB1Indicators> {
  const { data } = await api.get<Partial<SapB1Indicators>>(SAP_B1_PATHS.indicators);
  return { indicators: Array.isArray(data?.indicators) ? data.indicators : [] };
}

export async function getSapB1View<V extends SapB1ViewName>(view: V, topN = 0): Promise<SapB1View<V>> {
  const { data } = await api.get<SapB1View<V>>(sapB1ViewPath(view, topN));
  return data;
}

export async function getLoadReconciliation(): Promise<SapB1LoadRow[] | null> {
  try {
    const { data } = await api.get<SapB1LoadRow[] | { data?: SapB1LoadRow[] }>(loadReconciliationPath());
    if (Array.isArray(data)) return data;
    return Array.isArray(data?.data) ? data.data : [];
  } catch (error) {
    if (isApiError(error) && error.status === 404) return null;
    throw error;
  }
}

export async function getSapB1BusinessParameters(): Promise<SapB1BusinessParameters> {
  const { data } = await api.get<SapB1BusinessParameters>(SAP_B1_PATHS.businessParameters);
  return data;
}

export async function saveSapB1BusinessParameters(text: string): Promise<SapB1SaveParametersResult> {
  const { data } = await api.put<SapB1SaveParametersResult>(SAP_B1_PATHS.businessParameters, { text });
  return data;
}

export async function uploadSapB1FinanceRun(csv: string): Promise<SapB1FinanceRunResult> {
  const { data } = await api.post<SapB1FinanceRunResult>(SAP_B1_PATHS.financeRuns, { csv });
  return data;
}

export async function getSapB1Recipients(): Promise<SapB1Recipients> {
  const { data } = await api.get<SapB1Recipients>(SAP_B1_PATHS.recipients);
  return data;
}

export async function addSapB1Recipient(email: string): Promise<{ added: boolean; email: string }> {
  const { data } = await api.post<{ added: boolean; email: string }>(SAP_B1_PATHS.recipients, { email });
  return data;
}

export async function removeSapB1Recipient(email: string): Promise<{ removed: boolean; email: string }> {
  const { data } = await api.delete<{ removed: boolean; email: string }>(recipientPath(email));
  return data;
}
