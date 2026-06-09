import { api } from "@/lib/api";

import type {
  ActivityPayload,
  CatalogPayload,
  Dashboard,
  ImpactPayload,
  LessonsPayload,
  SfGoldKpisPayload,
  ThresholdPayload,
} from "./types";

export const CONTROL_ROOM_PATHS = {
  dashboard: "/api/control-room/dashboard",
  lessons: "/api/control-room/lessons",
  thresholds: "/api/control-room/thresholds",
  sfGoldKpis: "/api/control-room/sap-successfactors/gold-kpis",
  sfGoldCatalog: "/api/catalog?cartridge=sap_successfactors&layer=gold",
} as const;

export async function getControlRoomDashboard(): Promise<Dashboard> {
  const response = await api.get<Dashboard>(CONTROL_ROOM_PATHS.dashboard);
  return response.data;
}

export async function getControlRoomActivity(itemId: string): Promise<ActivityPayload> {
  const response = await api.get<ActivityPayload>(
    `/api/control-room/items/${encodeURIComponent(itemId)}/activity`,
  );
  return response.data;
}

export async function getControlRoomImpact(itemId: string): Promise<ImpactPayload> {
  const response = await api.get<ImpactPayload>(
    `/api/control-room/items/${encodeURIComponent(itemId)}/impact`,
  );
  return response.data;
}

export async function getControlRoomLessons(cartridgeId?: string): Promise<LessonsPayload> {
  const params = new URLSearchParams();
  if (cartridgeId) params.set("cartridge_id", cartridgeId);
  const query = params.toString();
  const response = await api.get<LessonsPayload>(
    `${CONTROL_ROOM_PATHS.lessons}${query ? `?${query}` : ""}`,
  );
  return response.data;
}

export async function getControlRoomThresholds(): Promise<ThresholdPayload> {
  const response = await api.get<ThresholdPayload>(CONTROL_ROOM_PATHS.thresholds);
  return response.data;
}

export async function getSuccessFactorsGoldKpis(): Promise<SfGoldKpisPayload> {
  const response = await api.get<SfGoldKpisPayload>(CONTROL_ROOM_PATHS.sfGoldKpis);
  return response.data;
}

export async function getSuccessFactorsGoldCatalog(): Promise<CatalogPayload> {
  const response = await api.get<CatalogPayload>(CONTROL_ROOM_PATHS.sfGoldCatalog);
  return response.data;
}

export async function getDatasetPreview(dataset: string, limit = 20): Promise<unknown> {
  const params = new URLSearchParams({ limit: String(limit) });
  const response = await api.get<unknown>(
    `/api/data/${encodeURIComponent(dataset)}?${params.toString()}`,
  );
  return response.data;
}
