import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import {
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
  previewSuccessFactorsTalentAction,
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

    await getControlRoomActivity("item 1/femsa");
    await getControlRoomImpact("item 1/femsa");

    expect(apiMock.get).toHaveBeenNthCalledWith(1, "/api/control-room/items/item%201%2Ffemsa/activity");
    expect(apiMock.get).toHaveBeenNthCalledWith(2, "/api/control-room/items/item%201%2Ffemsa/impact");
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
    apiMock.post.mockResolvedValue({ data: {}, status: 200, headers: new Headers(), requestId: "r" });

    await getSuccessFactorsTalentOverview();
    await getSuccessFactorsTalentNineBox();
    await getSuccessFactorsTalentBoxRoster("alto impacto/core");
    await getSuccessFactorsTalentAnomalies();
    await getSuccessFactorsTalentMetadataReadiness();
    await previewSuccessFactorsTalentAction({ action_id: "talent_calibration_sensitivity", box_id: "core" });

    expect(apiMock.get).toHaveBeenNthCalledWith(1, "/api/control-room/sap-successfactors/talent/overview");
    expect(apiMock.get).toHaveBeenNthCalledWith(2, "/api/control-room/sap-successfactors/talent/9box");
    expect(apiMock.get).toHaveBeenNthCalledWith(3, "/api/control-room/sap-successfactors/talent/9box/alto%20impacto%2Fcore");
    expect(apiMock.get).toHaveBeenNthCalledWith(4, "/api/control-room/sap-successfactors/talent/anomalies");
    expect(apiMock.get).toHaveBeenNthCalledWith(5, "/api/control-room/sap-successfactors/talent/metadata-readiness");
    expect(apiMock.post).toHaveBeenCalledWith(
      "/api/control-room/sap-successfactors/talent/actions/preview",
      { action_id: "talent_calibration_sensitivity", box_id: "core" },
    );
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
});
