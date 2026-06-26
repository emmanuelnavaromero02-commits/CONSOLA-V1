import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import {
  createSyncNowRequestId,
  getActiveCartridgeSyncRun,
  hasSyncRunId,
  startCartridgeSyncNow,
} from "./sync-now";

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
  vi.unstubAllGlobals();
});

describe("sync-now client", () => {
  it("creates backend-safe request ids for sync retries", () => {
    vi.stubGlobal("crypto", { randomUUID: () => "uuid-fixed" });

    const requestId = createSyncNowRequestId("sap success/factors", {
      mode: "incremental",
      target: "talent",
    });

    expect(requestId).toBe("sync-now:sap_success_factors:incremental:talent:uuid-fixed");
    expect(requestId).toMatch(/^[A-Za-z0-9_.:-]{1,128}$/);
  });

  it("posts a request id so the backend can reuse the same sync run", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "uuid-fixed" });
    apiMock.post.mockResolvedValue({
      data: {
        run_id: "sync_now:sap_successfactors:abc",
        cartridge_id: "sap_successfactors",
        status: "running",
        mode: "incremental",
        target: "all",
        steps: [],
        triggered_entities: [],
        errors: [],
        control_room_ready: false,
      },
      status: 200,
      headers: new Headers(),
      requestId: "r",
    });

    await startCartridgeSyncNow("sap_successfactors", {
      mode: "incremental",
      target: "all",
    });

    expect(apiMock.post).toHaveBeenCalledWith(
      "/api/cartridges/sap_successfactors/sync-now",
      {
        mode: "incremental",
        target: "all",
        request_id: "sync-now:sap_successfactors:incremental:all:uuid-fixed",
      },
    );
  });

  it("gets the active sync run with explicit mode, target, and connection", async () => {
    apiMock.get.mockResolvedValue({
      data: {
        run_id: "sync_now:sap_successfactors:active",
        cartridge_id: "sap_successfactors",
        status: "running",
        mode: "incremental",
        target: "talent",
        steps: [],
        triggered_entities: [],
        errors: [],
        control_room_ready: false,
      },
      status: 200,
      headers: new Headers(),
      requestId: "r",
    });

    await getActiveCartridgeSyncRun("sap_successfactors", {
      mode: "incremental",
      target: "talent",
      conn_id: "femsa_sf",
    });

    expect(apiMock.get).toHaveBeenCalledWith(
      "/api/cartridges/sap_successfactors/sync-runs/active?mode=incremental&target=talent&conn_id=femsa_sf",
    );
  });

  it("identifies inactive sync payloads without a run id", () => {
    expect(hasSyncRunId(null)).toBe(false);
    expect(
      hasSyncRunId({
        run_id: null,
        cartridge_id: "sap_successfactors",
        status: "skipped",
        active: false,
        mode: "incremental",
        target: "all",
        steps: [],
        triggered_entities: [],
        errors: [],
        control_room_ready: false,
      }),
    ).toBe(false);
    expect(
      hasSyncRunId({
        run_id: "sync_now:sap_successfactors:active",
        cartridge_id: "sap_successfactors",
        status: "running",
        active: true,
        mode: "incremental",
        target: "all",
        steps: [],
        triggered_entities: [],
        errors: [],
        control_room_ready: false,
      }),
    ).toBe(true);
  });
});
