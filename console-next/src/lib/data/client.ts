import { api } from "@/lib/api";
import type {
  AutoProfileInput,
  AutoProfileStatus,
  BronzeQueryInput,
  BronzeQueryPayload,
  CatalogEdgeInput,
  CatalogEntryInput,
  CatalogFilters,
  CatalogRelationshipInput,
  DataCatalogPayload,
  DatasetRow,
  LineagePayload,
} from "./types";

function buildQuery(filters: CatalogFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (typeof value === "string" && value.trim()) {
      params.set(key, value.trim());
    } else if (value === true) {
      params.set(key, "true");
    }
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

function normalizeCatalog(payload: Partial<DataCatalogPayload> | null | undefined): DataCatalogPayload {
  return {
    datasets: payload?.datasets ?? {},
    relationships: payload?.relationships ?? [],
    annotations_degraded: payload?.annotations_degraded === true,
  };
}

export async function getDataCatalog(filters: CatalogFilters = {}): Promise<DataCatalogPayload> {
  const { data } = await api.get<Partial<DataCatalogPayload>>(`/api/catalog${buildQuery(filters)}`);
  return normalizeCatalog(data);
}

export async function getDatasetRows(dataset: string, limit = 20): Promise<DatasetRow[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  const { data } = await api.get<DatasetRow[] | { data?: DatasetRow[] }>(
    `/api/data/${encodeURIComponent(dataset)}?${params.toString()}`,
  );
  if (Array.isArray(data)) return data;
  return Array.isArray(data.data) ? data.data : [];
}

export async function upsertCatalogEntry(entry: CatalogEntryInput): Promise<unknown> {
  const { data } = await api.post<unknown>("/api/catalog/entries", { entries: [entry] });
  return data;
}

export async function registerCatalogRelationship(relationship: CatalogRelationshipInput): Promise<unknown> {
  const { data } = await api.post<unknown>("/api/catalog/relationships", relationship);
  return data;
}

export async function autoProfileCatalog(input: AutoProfileInput = {}): Promise<AutoProfileStatus> {
  const body: AutoProfileInput = {};
  if (input.cartridge?.trim()) body.cartridge = input.cartridge.trim();
  if (input.include_sources) body.include_sources = true;
  if (input.since) body.since = input.since;
  const { data } = await api.post<AutoProfileStatus>("/api/catalog/auto-profile", body);
  return data;
}

export async function rejectCatalogRelationship(edge: CatalogEdgeInput): Promise<unknown> {
  const { data } = await api.post<unknown>("/api/catalog/relationships/reject", {
    from_dataset: edge.from_dataset,
    from_column: edge.from_column,
    to_dataset: edge.to_dataset,
    to_column: edge.to_column,
  });
  return data;
}

export async function getDataLineage(cartridge?: string): Promise<LineagePayload> {
  const query = cartridge?.trim() ? `?cartridge=${encodeURIComponent(cartridge.trim())}` : "";
  const { data } = await api.get<LineagePayload>(`/api/lineage${query}`);
  return {
    nodes: data.nodes ?? [],
    edges: data.edges ?? [],
  };
}

export async function queryBronze(input: BronzeQueryInput): Promise<BronzeQueryPayload> {
  const { data } = await api.post<BronzeQueryPayload>("/api/bronze/query", input);
  return data;
}
