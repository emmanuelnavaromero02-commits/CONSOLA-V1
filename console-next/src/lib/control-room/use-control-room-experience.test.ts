import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import type { ControlRoomExperienceV2 } from "./experience-contract";
import {
  controlRoomExperienceKey,
  controlRoomExperienceQueryOptions,
} from "./use-control-room-experience";

const payload: ControlRoomExperienceV2 = {
  schema_version: "control-room-experience/v2",
  generated_at: "2026-07-25T12:30:00Z",
  sections: [],
};

describe("controlRoomExperienceQueryOptions", () => {
  it("keys cache entries by workspace and bounds automatic refetch", () => {
    const fetcher = vi.fn(async () => payload);
    const options = controlRoomExperienceQueryOptions("workspace-a", fetcher);

    expect(options.queryKey).toEqual([
      "control-room",
      "experience",
      "v2",
      "workspace-a",
    ]);
    expect(controlRoomExperienceKey("workspace-b")).not.toEqual(options.queryKey);
    expect(options.retry).toBe(false);
    expect(options.refetchOnMount).toBe(true);
    expect(options.refetchOnReconnect).toBe(false);
    expect(options.refetchOnWindowFocus).toBe(false);
    expect(options.refetchInterval).toBe(false);
    expect(options.staleTime).toBe(15_000);
  });

  it("reuses a fresh remount and fetches exactly once after the cache is stale", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { gcTime: Number.POSITIVE_INFINITY, retry: false } },
    });
    const fetcher = vi.fn(async () => payload);
    const options = controlRoomExperienceQueryOptions("workspace-a", fetcher);
    const key = controlRoomExperienceKey("workspace-a");

    const firstObserver = new QueryObserver(client, options);
    const stopFirst = firstObserver.subscribe(() => undefined);
    await vi.waitFor(() => expect(firstObserver.getCurrentResult().isSuccess).toBe(true));
    expect(fetcher).toHaveBeenCalledTimes(1);
    stopFirst();

    const freshObserver = new QueryObserver(client, options);
    const stopFresh = freshObserver.subscribe(() => undefined);
    await Promise.resolve();
    expect(fetcher).toHaveBeenCalledTimes(1);
    stopFresh();

    client.setQueryData(key, payload, { updatedAt: Date.now() - 15_001 });
    const staleObserver = new QueryObserver(client, options);
    const stopStale = staleObserver.subscribe(() => undefined);
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    stopStale();
    client.clear();
  });

  it("keeps workspace cache entries isolated", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const fetcherA = vi.fn(async () => payload);
    const fetcherB = vi.fn(async () => payload);

    await client.fetchQuery(controlRoomExperienceQueryOptions("workspace-a", fetcherA));
    await client.fetchQuery(controlRoomExperienceQueryOptions("workspace-b", fetcherB));

    expect(fetcherA).toHaveBeenCalledWith();
    expect(fetcherB).toHaveBeenCalledWith();
    expect(client.getQueryData(controlRoomExperienceKey("workspace-a"))).toEqual(payload);
    expect(client.getQueryData(controlRoomExperienceKey("workspace-b"))).toEqual(payload);
    client.clear();
  });

  it("retains the last payload when a stale remount refresh fails", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { gcTime: Number.POSITIVE_INFINITY, retry: false } },
    });
    let fail = false;
    const fetcher = vi.fn(async () => {
      if (fail) throw new Error("internal failure");
      return payload;
    });
    const options = controlRoomExperienceQueryOptions("workspace-a", fetcher);
    const key = controlRoomExperienceKey("workspace-a");
    const firstObserver = new QueryObserver(client, options);
    const stopFirst = firstObserver.subscribe(() => undefined);
    await vi.waitFor(() => expect(firstObserver.getCurrentResult().isSuccess).toBe(true));
    stopFirst();

    fail = true;
    client.setQueryData(key, payload, { updatedAt: Date.now() - 15_001 });
    const staleObserver = new QueryObserver(client, options);
    const stopStale = staleObserver.subscribe(() => undefined);
    await vi.waitFor(() =>
      expect(staleObserver.getCurrentResult().isRefetchError).toBe(true),
    );

    expect(staleObserver.getCurrentResult().data).toEqual(payload);
    expect(fetcher).toHaveBeenCalledTimes(2);
    stopStale();
    client.clear();
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
    client.clear();
  });
});
