import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import {
  CONTROL_ROOM_PATHS,
  createItemDecision,
  getControlRoomActivity,
  getControlRoomDashboard,
  getControlRoomImpact,
  getControlRoomLessons,
  getControlRoomThresholds,
  getMarketDecisionValidation,
  runMarketDecisionValidation,
  getSuccessFactorsDecisionModel,
  getSuccessFactorsGoldKpis,
  getSuccessFactorsTalentAnomalies,
  getSuccessFactorsTalentBoxRoster,
  getSuccessFactorsTalentKpis,
  getSuccessFactorsTalentMetadataReadiness,
  getSuccessFactorsTalentNineBox,
  getSuccessFactorsTalentOverview,
  listAlerts,
  markAlertFalsePositive,
} from "./client";

vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const apiMock = vi.mocked(api);

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("control-room client", () => {
  it("uses existing executive read endpoints for dashboard, KPIs and thresholds", async () => {
    apiMock.get.mockResolvedValue({ data: {}, status: 200, headers: new Headers(), requestId: "r" });

    await getControlRoomDashboard();
    await getSuccessFactorsGoldKpis();
    await getSuccessFactorsTalentKpis();
    await getSuccessFactorsDecisionModel();
    await getControlRoomThresholds();

    expect(apiMock.get).toHaveBeenNthCalledWith(1, "/api/control-room/dashboard");
    expect(apiMock.get).toHaveBeenNthCalledWith(2, "/api/control-room/sap-successfactors/gold-kpis");
    expect(apiMock.get).toHaveBeenNthCalledWith(3, "/api/control-room/sap-successfactors/talent-kpis");
    expect(apiMock.get).toHaveBeenNthCalledWith(4, "/api/semantic?cartridge=sap_successfactors");
    expect(apiMock.get).toHaveBeenNthCalledWith(5, "/api/control-room/thresholds");
  });

  it("keeps item-scoped activity and impact endpoints encoded", async () => {
    apiMock.get.mockResolvedValue({ data: {}, status: 200, headers: new Headers(), requestId: "r" });

    await getControlRoomActivity("item 1/acmeco");
    await getControlRoomImpact("item 1/acmeco");

    expect(apiMock.get).toHaveBeenNthCalledWith(1, "/api/control-room/items/item%201%2Facmeco/activity");
    expect(apiMock.get).toHaveBeenNthCalledWith(2, "/api/control-room/items/item%201%2Facmeco/impact");
  });

  it("keeps optional cartridge scope on lessons without exposing data preview routes", async () => {
    apiMock.get.mockResolvedValue({ data: {}, status: 200, headers: new Headers(), requestId: "r" });

    await getControlRoomLessons("sap successfactors");
    await getControlRoomLessons();

    expect(apiMock.get).toHaveBeenNthCalledWith(1, "/api/control-room/lessons?cartridge_id=sap+successfactors");
    expect(apiMock.get).toHaveBeenNthCalledWith(2, "/api/control-room/lessons");
    expect(apiMock.get).toHaveBeenCalledTimes(2);
  });

  it("uses native SuccessFactors Talent endpoints without embedding HTML", async () => {
    apiMock.get.mockResolvedValue({ data: {}, status: 200, headers: new Headers(), requestId: "r" });

    await getSuccessFactorsTalentOverview();
    await getSuccessFactorsTalentNineBox();
    await getSuccessFactorsTalentBoxRoster("alto impacto/core");
    await getSuccessFactorsTalentAnomalies();
    await getSuccessFactorsTalentMetadataReadiness();

    expect(apiMock.get).toHaveBeenNthCalledWith(1, "/api/control-room/sap-successfactors/talent/overview");
    expect(apiMock.get).toHaveBeenNthCalledWith(2, "/api/control-room/sap-successfactors/talent/9box");
    expect(apiMock.get).toHaveBeenNthCalledWith(3, "/api/control-room/sap-successfactors/talent/9box/alto%20impacto%2Fcore");
    expect(apiMock.get).toHaveBeenNthCalledWith(4, "/api/control-room/sap-successfactors/talent/anomalies");
    expect(apiMock.get).toHaveBeenNthCalledWith(5, "/api/control-room/sap-successfactors/talent/metadata-readiness");
    expect(apiMock.post).not.toHaveBeenCalled();
    expect(JSON.stringify(CONTROL_ROOM_PATHS)).not.toContain("talent/actions/preview");
  });

  it("uses scoped market validation read and run endpoints", async () => {
    apiMock.get.mockResolvedValue({ data: {}, status: 200, headers: new Headers(), requestId: "r" });
    apiMock.post.mockResolvedValue({ data: {}, status: 200, headers: new Headers(), requestId: "r" });

    await getMarketDecisionValidation();
    await runMarketDecisionValidation();

    expect(apiMock.get).toHaveBeenCalledWith(
      "/api/control-room/sap-successfactors/market-validation",
    );
    expect(apiMock.post).toHaveBeenCalledWith(
      "/api/control-room/sap-successfactors/market-validation/run",
    );
  });

  it("lists alerts and records decisions and false positives on encoded item ids", async () => {
    apiMock.get.mockResolvedValue({ data: { alerts: [] }, status: 200, headers: new Headers(), requestId: "r" });
    apiMock.post.mockResolvedValue({ data: { item: {} }, status: 200, headers: new Headers(), requestId: "r" });

    await expect(listAlerts()).resolves.toEqual({ alerts: [] });
    await createItemDecision("agent_alert:ab/12");
    await markAlertFalsePositive("agent_alert:ab/12", "  lote ya vendido  ");
    await markAlertFalsePositive("agent_alert:cd");
    await markAlertFalsePositive("agent_alert:ef", "   ");

    expect(apiMock.get).toHaveBeenCalledWith("/api/control-room/alerts");
    expect(apiMock.post).toHaveBeenNthCalledWith(1, "/api/control-room/items/agent_alert%3Aab%2F12/decision");
    expect(apiMock.post.mock.calls[0]).toHaveLength(1);
    expect(apiMock.post).toHaveBeenNthCalledWith(
      2,
      "/api/control-room/alerts/agent_alert%3Aab%2F12/false-positive",
      { note: "lote ya vendido" },
    );
    expect(apiMock.post).toHaveBeenNthCalledWith(3, "/api/control-room/alerts/agent_alert%3Acd/false-positive", {});
    expect(apiMock.post).toHaveBeenNthCalledWith(4, "/api/control-room/alerts/agent_alert%3Aef/false-positive", {});
  });
});
