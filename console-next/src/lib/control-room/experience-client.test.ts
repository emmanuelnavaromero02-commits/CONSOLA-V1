import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import {
  CONTROL_ROOM_EXPERIENCE_ENDPOINT,
  getControlRoomExperience,
} from "./experience-client";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn() } }));

const apiGet = vi.mocked(api.get);
const payload = {
  schema_version: "control-room-experience/v1",
  generated_at: "2026-07-25T12:30:00Z",
  sections: [],
};

describe("getControlRoomExperience", () => {
  beforeEach(() => apiGet.mockReset());

  it("performs exactly one GET against the Experience endpoint", async () => {
    apiGet.mockResolvedValue({
      data: payload,
      status: 200,
      headers: new Headers(),
      requestId: "request-1",
    });

    await expect(getControlRoomExperience()).resolves.toEqual(payload);
    expect(apiGet).toHaveBeenCalledTimes(1);
    expect(apiGet).toHaveBeenCalledWith(CONTROL_ROOM_EXPERIENCE_ENDPOINT);
  });

  it("rejects private scope fields returned by the server", async () => {
    apiGet.mockResolvedValue({
      data: {
        ...payload,
        scope: { tenant_id: "tenant-a", workspace_id: "workspace-b" },
      },
      status: 200,
      headers: new Headers(),
      requestId: "request-2",
    });

    await expect(getControlRoomExperience()).rejects.toThrow();
  });

  it("fails closed on an invalid response contract", async () => {
    apiGet.mockResolvedValue({
      data: { ...payload, internal_detail: "must-not-render" },
      status: 200,
      headers: new Headers(),
      requestId: "request-3",
    });

    await expect(getControlRoomExperience()).rejects.toThrow();
  });
});
