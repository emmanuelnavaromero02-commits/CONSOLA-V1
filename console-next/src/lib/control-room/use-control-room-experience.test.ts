import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import type { ControlRoomExperience } from "./experience-contract";
import {
  controlRoomExperienceKey,
  controlRoomExperienceQueryOptions,
} from "./use-control-room-experience";

const payload: ControlRoomExperience = {
  schema_version: "control-room-experience/v1",
  generated_at: "2026-07-25T12:30:00Z",
  scope: { tenant_id: "tenant-a", workspace_id: "workspace-a" },
  sections: [],
};

describe("controlRoomExperienceQueryOptions", () => {
  it("keys cache entries by workspace and disables automatic refetch", () => {
    const fetcher = vi.fn(async () => payload);
    const options = controlRoomExperienceQueryOptions("workspace-a", fetcher);

    expect(options.queryKey).toEqual(["control-room", "experience", "workspace-a"]);
    expect(controlRoomExperienceKey("workspace-b")).not.toEqual(options.queryKey);
    expect(options.retry).toBe(false);
    expect(options.refetchOnMount).toBe(false);
    expect(options.refetchOnReconnect).toBe(false);
    expect(options.refetchOnWindowFocus).toBe(false);
    expect(options.refetchInterval).toBe(false);
  });

  it("retains the last valid payload after an explicit refresh fails", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const key = controlRoomExperienceKey("workspace-a");

    await client.fetchQuery(
      controlRoomExperienceQueryOptions("workspace-a", async () => payload),
    );
    await client.invalidateQueries({ queryKey: key });
    await expect(
      client.fetchQuery({
        ...controlRoomExperienceQueryOptions("workspace-a", async () => {
          throw new Error("internal failure");
        }),
        staleTime: 0,
      }),
    ).rejects.toThrow("internal failure");

    expect(client.getQueryData(key)).toEqual(payload);
  });
});
