import { api } from "@/lib/api";

import type {
  ActivityPayload,
  ControlRoomAgentsOpsPayload,
  Dashboard,
  ImpactPayload,
  LessonsPayload,
  MarketDecisionValidationPayload,
  SfDecisionModelPayload,
  SfGoldKpisPayload,
  SfTalentAnomaliesPayload,
  SfTalentKpisPayload,
  SfTalentMetadataReadinessPayload,
  SfTalentNineBoxPayload,
  SfTalentOverviewPayload,
  SfTalentRosterPayload,
  ThresholdPayload,
} from "./types";

export const CONTROL_ROOM_PATHS = {
  dashboard: "/api/control-room/dashboard",
  lessons: "/api/control-room/lessons",
  thresholds: "/api/control-room/thresholds",
  agentsOps: "/api/control-room/agents/ops",
  sfGoldKpis: "/api/control-room/sap-successfactors/gold-kpis",
  sfTalentKpis: "/api/control-room/sap-successfactors/talent-kpis",
  sfTalentOverview: "/api/control-room/sap-successfactors/talent/overview",
  sfTalentNineBox: "/api/control-room/sap-successfactors/talent/9box",
  sfTalentAnomalies: "/api/control-room/sap-successfactors/talent/anomalies",
  sfTalentMetadataReadiness: "/api/control-room/sap-successfactors/talent/metadata-readiness",
  sfMarketValidation: "/api/control-room/sap-successfactors/market-validation",
  sfMarketValidationRun: "/api/control-room/sap-successfactors/market-validation/run",
  sfDecisionModel: "/api/semantic?cartridge=sap_successfactors",
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

export async function getControlRoomAgentsOps(): Promise<ControlRoomAgentsOpsPayload> {
  const response = await api.get<ControlRoomAgentsOpsPayload>(CONTROL_ROOM_PATHS.agentsOps);
  return response.data;
}

export async function getSuccessFactorsGoldKpis(): Promise<SfGoldKpisPayload> {
  const response = await api.get<SfGoldKpisPayload>(CONTROL_ROOM_PATHS.sfGoldKpis);
  return response.data;
}

export async function getSuccessFactorsTalentKpis(): Promise<SfTalentKpisPayload> {
  const response = await api.get<SfTalentKpisPayload>(CONTROL_ROOM_PATHS.sfTalentKpis);
  return response.data;
}

export async function getSuccessFactorsTalentOverview(): Promise<SfTalentOverviewPayload> {
  const response = await api.get<SfTalentOverviewPayload>(CONTROL_ROOM_PATHS.sfTalentOverview);
  return response.data;
}

export async function getSuccessFactorsTalentNineBox(): Promise<SfTalentNineBoxPayload> {
  const response = await api.get<SfTalentNineBoxPayload>(CONTROL_ROOM_PATHS.sfTalentNineBox);
  return response.data;
}

export async function getSuccessFactorsTalentBoxRoster(boxId: string): Promise<SfTalentRosterPayload> {
  const response = await api.get<SfTalentRosterPayload>(
    `${CONTROL_ROOM_PATHS.sfTalentNineBox}/${encodeURIComponent(boxId)}`,
  );
  return response.data;
}

export async function getSuccessFactorsTalentAnomalies(): Promise<SfTalentAnomaliesPayload> {
  const response = await api.get<SfTalentAnomaliesPayload>(CONTROL_ROOM_PATHS.sfTalentAnomalies);
  return response.data;
}

export async function getSuccessFactorsTalentMetadataReadiness(): Promise<SfTalentMetadataReadinessPayload> {
  const response = await api.get<SfTalentMetadataReadinessPayload>(
    CONTROL_ROOM_PATHS.sfTalentMetadataReadiness,
  );
  return response.data;
}

export async function getSuccessFactorsDecisionModel(): Promise<SfDecisionModelPayload> {
  const response = await api.get<SfDecisionModelPayload>(CONTROL_ROOM_PATHS.sfDecisionModel);
  return response.data;
}

export async function getMarketDecisionValidation(): Promise<MarketDecisionValidationPayload> {
  const response = await api.get<MarketDecisionValidationPayload>(
    CONTROL_ROOM_PATHS.sfMarketValidation,
  );
  return response.data;
}

export async function runMarketDecisionValidation(): Promise<MarketDecisionValidationPayload> {
  const response = await api.post<MarketDecisionValidationPayload>(
    CONTROL_ROOM_PATHS.sfMarketValidationRun,
  );
  return response.data;
}
