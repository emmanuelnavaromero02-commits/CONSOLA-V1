// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ControlRoomFreshness } from "./experience-contract";
import { controlRoomExperienceKey } from "./use-control-room-experience";
import {
  MAX_EXPERIENCE_AGE_MS,
  controlRoomFreshnessKey,
  controlRoomFreshnessQueryOptions,
  decideExperienceRefresh,
  invalidateControlRoomLive,
  useControlRoomLive,
  type LiveExperienceHandle,
} from "./use-control-room-live";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function freshness(fingerprint: string): ControlRoomFreshness {
  return {
    schema_version: "control-room-freshness/v1",
    fingerprint: fingerprint.repeat(64).slice(0, 64),
    checked_at: "2026-09-26T10:00:00Z",
    data_refreshed_at: null,
  };
}

describe("controlRoomFreshnessQueryOptions", () => {
  it("polls every 30 s only while visible and never caches", () => {
    const options = controlRoomFreshnessQueryOptions("workspace-a", vi.fn());

    expect(options.queryKey).toEqual(["control-room", "freshness", "v1", "workspace-a"]);
    expect(options.queryKey).not.toEqual(controlRoomExperienceKey("workspace-a"));
    expect(options.retry).toBe(false);
    expect(options.refetchInterval).toBe(30_000);
    expect(options.refetchIntervalInBackground).toBe(false);
    expect(options.refetchOnWindowFocus).toBe(true);
    expect(options.staleTime).toBe(0);
    expect(controlRoomFreshnessKey(null)).toEqual([
      "control-room",
      "freshness",
      "v1",
      "unscoped",
    ]);
  });
});

describe("decideExperienceRefresh", () => {
  const now = 1_000_000_000;
  const base = {
    state: { baseline: "a", pending: false },
    fingerprint: "a",
    experienceUpdatedAt: now - 1_000,
    experienceFetching: false,
    paused: false,
    now,
  };

  it("adopts the first fingerprint without refetching", () => {
    expect(
      decideExperienceRefresh({ ...base, state: { baseline: null, pending: false } }),
    ).toEqual({ state: { baseline: "a", pending: false }, refetch: false });
  });

  it("refetches once when the fingerprint changes", () => {
    expect(decideExperienceRefresh({ ...base, fingerprint: "b" })).toEqual({
      state: { baseline: "b", pending: false },
      refetch: true,
    });
  });

  it("defers while a dialog is open and releases the pending refresh later", () => {
    const deferred = decideExperienceRefresh({ ...base, fingerprint: "b", paused: true });
    expect(deferred).toEqual({ state: { baseline: "b", pending: true }, refetch: false });
    expect(decideExperienceRefresh({ ...base, state: deferred.state, fingerprint: "b" })).toEqual(
      { state: { baseline: "b", pending: false }, refetch: true },
    );
  });

  it("refreshes data older than ten minutes before handles expire", () => {
    expect(
      decideExperienceRefresh({
        ...base,
        experienceUpdatedAt: now - MAX_EXPERIENCE_AGE_MS,
      }).refetch,
    ).toBe(true);
    expect(MAX_EXPERIENCE_AGE_MS).toBeLessThan(15 * 60_000);
  });

  it("never stacks a refresh on an in-flight load but remembers the change", () => {
    const inFlight = decideExperienceRefresh({
      ...base,
      fingerprint: "b",
      experienceFetching: true,
    });
    expect(inFlight).toEqual({ state: { baseline: "b", pending: true }, refetch: false });
    expect(
      decideExperienceRefresh({ ...base, state: inFlight.state, fingerprint: "b" }),
    ).toEqual({ state: { baseline: "b", pending: false }, refetch: true });
    expect(
      decideExperienceRefresh({ ...base, fingerprint: "a", experienceFetching: true }),
    ).toEqual({ state: { baseline: "a", pending: false }, refetch: false });
  });

  it("does not treat a missing load as aged", () => {
    expect(decideExperienceRefresh({ ...base, experienceUpdatedAt: 0 }).refetch).toBe(false);
  });
});

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;
const probe: { latest: ReturnType<typeof useControlRoomLive> | null } = {
  latest: null,
};

function Harness(props: {
  workspaceId: string;
  experience: LiveExperienceHandle;
  paused: boolean;
  fetcher: () => Promise<ControlRoomFreshness>;
}) {
  const live = useControlRoomLive(props);
  useEffect(() => {
    probe.latest = live;
  });
  return null;
}

async function render(props: Parameters<typeof Harness>[0]) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Harness {...props} />
      </QueryClientProvider>,
    );
  });
}

async function refetchFreshness(workspaceId = "workspace-a") {
  const key = controlRoomFreshnessKey(workspaceId);
  await act(async () => {
    await queryClient.refetchQueries({ queryKey: key });
  });
  await vi.waitFor(() =>
    expect(probe.latest?.checkedAt).toBe(queryClient.getQueryState(key)?.dataUpdatedAt),
  );
  await act(async () => undefined);
}

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  probe.latest = null;
});

afterEach(async () => {
  await act(async () => root.unmount());
  queryClient.clear();
  container.remove();
});

describe("useControlRoomLive", () => {
  function experience(updatedAt = Date.now()) {
    return {
      dataUpdatedAt: updatedAt,
      isFetching: false,
      refetch: vi.fn(async () => undefined),
    };
  }

  it("refetches the experience only when the fingerprint moves", async () => {
    const fingerprints = ["a", "a", "b"];
    const fetcher = vi.fn(async () => freshness(fingerprints.shift() ?? "b"));
    const handle = experience();
    await render({ workspaceId: "workspace-a", experience: handle, paused: false, fetcher });
    await vi.waitFor(() => expect(probe.latest?.checkedAt).not.toBeNull());
    expect(handle.refetch).not.toHaveBeenCalled();

    await refetchFreshness();
    expect(handle.refetch).not.toHaveBeenCalled();
    await refetchFreshness();
    expect(handle.refetch).toHaveBeenCalledTimes(1);
    expect(probe.latest?.offline).toBe(false);
  });

  it("holds a change while paused and applies it when the dialog closes", async () => {
    const fingerprints = ["a", "b"];
    const fetcher = vi.fn(async () => freshness(fingerprints.shift() ?? "b"));
    const handle = experience();
    await render({ workspaceId: "workspace-a", experience: handle, paused: true, fetcher });
    await vi.waitFor(() => expect(probe.latest?.checkedAt).not.toBeNull());
    await refetchFreshness();
    expect(handle.refetch).not.toHaveBeenCalled();

    await render({ workspaceId: "workspace-a", experience: handle, paused: false, fetcher });
    expect(handle.refetch).toHaveBeenCalledTimes(1);
  });

  it("refreshes an experience loaded more than ten minutes ago", async () => {
    const fetcher = vi.fn(async () => freshness("a"));
    const handle = experience(Date.now() - MAX_EXPERIENCE_AGE_MS - 1);
    await render({ workspaceId: "workspace-a", experience: handle, paused: false, fetcher });
    await vi.waitFor(() => expect(handle.refetch).toHaveBeenCalledTimes(1));
  });

  it("resets the baseline when the workspace changes", async () => {
    const fetcherA = vi.fn(async () => freshness("a"));
    const fetcherB = vi.fn(async () => freshness("b"));
    const handle = experience();
    await render({
      workspaceId: "workspace-a",
      experience: handle,
      paused: false,
      fetcher: fetcherA,
    });
    await vi.waitFor(() => expect(probe.latest?.checkedAt).not.toBeNull());
    await render({
      workspaceId: "workspace-b",
      experience: handle,
      paused: false,
      fetcher: fetcherB,
    });
    await vi.waitFor(() => expect(fetcherB).toHaveBeenCalled());
    await act(async () => undefined);

    expect(handle.refetch).not.toHaveBeenCalled();
  });

  it("manual refresh reloads both queries and offline keeps the last check", async () => {
    let fail = false;
    const fetcher = vi.fn(async () => {
      if (fail) throw new Error("network down");
      return freshness("a");
    });
    const handle = experience();
    await render({ workspaceId: "workspace-a", experience: handle, paused: false, fetcher });
    await vi.waitFor(() => expect(probe.latest?.checkedAt).not.toBeNull());
    const checkedAt = probe.latest?.checkedAt;

    fail = true;
    await act(async () => probe.latest?.refreshAll());
    await vi.waitFor(() => expect(probe.latest?.offline).toBe(true));

    expect(handle.refetch).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(probe.latest?.checkedAt).toBe(checkedAt);
  });
});

describe("invalidateControlRoomLive", () => {
  it("invalidates exactly the active experience and freshness entries", async () => {
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");

    await invalidateControlRoomLive(client, "workspace-a");

    expect(invalidate).toHaveBeenCalledWith({
      queryKey: controlRoomExperienceKey("workspace-a"),
      exact: true,
      refetchType: "active",
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: controlRoomFreshnessKey("workspace-a"),
      exact: true,
      refetchType: "active",
    });
    client.clear();
  });
});
