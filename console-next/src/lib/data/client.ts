import { api } from "@/lib/api";
import type {
  BronzeQueryInput,
  BronzeQueryPayload,
  CatalogEntryInput,
  CatalogFilters,
  CatalogRelationshipInput,
  DataCatalogPayload,
  LineagePayload,
} from "./types";

function buildQuery(filters: CatalogFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (typeof value === "string" && value.trim()) {
      params.set(key, value.trim());
    }
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

function normalizeCatalog(payload: Partial<DataCatalogPayload> | null | undefined): DataCatalogPayload {
  return {
    datasets: payload?.datasets ?? {},
    relationships: payload?.relationships ?? [],
  };
}

export async function getDataCatalog(filters: CatalogFilters = {}): Promise<DataCatalogPayload> {
  const { data } = await api.get<Partial<DataCatalogPayload>>(`/api/catalog${buildQuery(filters)}`);
  return normalizeCatalog(data);
}

export async function upsertCatalogEntry(entry: CatalogEntryInput): Promise<unknown> {
  const { data } = await api.post<unknown>("/api/catalog/entries", { entries: [entry] });
  return data;
}

export async function registerCatalogRelationship(relationship: CatalogRelationshipInput): Promise<unknown> {
  const { data } = await api.post<unknown>("/api/catalog/relationships", relationship);
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
