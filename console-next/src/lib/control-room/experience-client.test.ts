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
  scope: { tenant_id: "tenant-a", workspace_id: "workspace-a" },
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

    await expect(getControlRoomExperience("workspace-a")).resolves.toEqual(payload);
    expect(apiGet).toHaveBeenCalledTimes(1);
    expect(apiGet).toHaveBeenCalledWith(CONTROL_ROOM_EXPERIENCE_ENDPOINT);
  });

  it("fails closed when the response belongs to another workspace", async () => {
    apiGet.mockResolvedValue({
      data: payload,
      status: 200,
      headers: new Headers(),
      requestId: "request-2",
    });

    await expect(getControlRoomExperience("workspace-b")).rejects.toThrow(
      "scope mismatch",
    );
  });

  it("fails closed on an invalid response contract", async () => {
    apiGet.mockResolvedValue({
      data: { ...payload, internal_detail: "must-not-render" },
      status: 200,
      headers: new Headers(),
      requestId: "request-3",
    });

    await expect(getControlRoomExperience("workspace-a")).rejects.toThrow();
  });
});
