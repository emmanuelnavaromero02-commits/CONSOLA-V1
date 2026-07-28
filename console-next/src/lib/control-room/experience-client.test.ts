import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import {
  CONTROL_ROOM_ACTION_PREVIEW_ENDPOINT,
  CONTROL_ROOM_EXPERIENCE_ENDPOINT,
  getControlRoomExperience,
  previewControlRoomExperienceAction,
} from "./experience-client";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));

const apiGet = vi.mocked(api.get);
const apiPost = vi.mocked(api.post);
const actionHandle = "a".repeat(64);
const payload = {
  schema_version: "control-room-experience/v2",
  generated_at: "2026-07-25T12:30:00Z",
  sections: [],
};

describe("getControlRoomExperience", () => {
  beforeEach(() => {
    apiGet.mockReset();
    apiPost.mockReset();
  });

  it("performs exactly one GET against the V2 Experience endpoint", async () => {
    apiGet.mockResolvedValue({
      data: payload,
      status: 200,
      headers: new Headers(),
      requestId: "request-1",
    });

    await expect(getControlRoomExperience()).resolves.toEqual(payload);
    expect(apiGet).toHaveBeenCalledTimes(1);
    expect(apiGet).toHaveBeenCalledWith(CONTROL_ROOM_EXPERIENCE_ENDPOINT);
    expect(CONTROL_ROOM_EXPERIENCE_ENDPOINT).toBe("/api/control-room/experience/v2");
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

describe("previewControlRoomExperienceAction", () => {
  it("performs exactly one POST with the exclusive action_handle body", async () => {
    const response = {
      action_handle: actionHandle,
      operation: "preview",
      status: "generated",
      message: "Preview generado; no se ejecuto ningun cambio externo.",
    };
    apiPost.mockResolvedValue({
      data: response,
      status: 200,
      headers: new Headers(),
      requestId: "request-preview",
    });

    await expect(previewControlRoomExperienceAction(actionHandle)).resolves.toEqual(
      response,
    );
    expect(apiPost).toHaveBeenCalledTimes(1);
    expect(apiPost).toHaveBeenCalledWith(CONTROL_ROOM_ACTION_PREVIEW_ENDPOINT, {
      action_handle: actionHandle,
    });
    expect(CONTROL_ROOM_ACTION_PREVIEW_ENDPOINT).toBe(
      "/api/control-room/actions/preview",
    );
  });

  it("rejects a valid-looking response for a different handle", async () => {
    apiPost.mockResolvedValue({
      data: {
        action_handle: "b".repeat(64),
        operation: "preview",
        status: "generated",
        message: "Preview generado; no se ejecuto ningun cambio externo.",
      },
      status: 200,
      headers: new Headers(),
      requestId: "request-mismatch",
    });

    await expect(previewControlRoomExperienceAction(actionHandle)).rejects.toThrow();
  });

  it("fails closed on an invalid preview response", async () => {
    apiPost.mockResolvedValue({
      data: { status: "executed", detail: actionHandle },
      status: 200,
      headers: new Headers(),
      requestId: "request-invalid",
    });

    await expect(previewControlRoomExperienceAction(actionHandle)).rejects.toThrow();
  });
});
