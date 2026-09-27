// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { controlRoomExperienceKey } from "./use-control-room-experience";
import { controlRoomFreshnessKey } from "./use-control-room-live";
import {
  canPersistControlRoom,
  useControlRoomRefresh,
} from "./use-control-room-refresh";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const boundary = vi.hoisted(() => ({ access: vi.fn(), refresh: vi.fn() }));

vi.mock("@/lib/admin-surfaces", () => ({ getMeAccess: boundary.access }));
vi.mock("@/lib/control-room/experience-client", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/lib/control-room/experience-client")
  >();
  return { ...actual, refreshControlRoomState: boundary.refresh };
});

const probe: { latest: ReturnType<typeof useControlRoomRefresh> | null } = {
  latest: null,
};
let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;

function Harness({ refetchOnly }: { refetchOnly: () => void }) {
  const value = useControlRoomRefresh({ workspaceId: "workspace-a", refetchOnly });
  useEffect(() => {
    probe.latest = value;
  });
  return null;
}

async function render(refetchOnly: () => void) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Harness refetchOnly={refetchOnly} />
      </QueryClientProvider>,
    );
  });
}

beforeEach(() => {
  boundary.access.mockReset();
  boundary.refresh.mockReset();
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

describe("useControlRoomRefresh", () => {
  it("writers persist through the audited POST and then reload both live queries", async () => {
    boundary.access.mockResolvedValue({ permissions: ["datasets.read", "control_room.write"] });
    boundary.refresh.mockResolvedValue({
      status: "refreshed",
      refreshed_at: "2026-09-26T12:00:00Z",
      message: "Información actualizada desde las fuentes.",
    });
    const refetchOnly = vi.fn();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    await render(refetchOnly);
    await vi.waitFor(() => expect(probe.latest?.canPersist).toBe(true));

    await act(async () => {
      probe.latest?.refresh();
      probe.latest?.refresh();
    });
    await vi.waitFor(() => expect(invalidate).toHaveBeenCalledTimes(2));

    expect(boundary.refresh).toHaveBeenCalledTimes(1);
    expect(refetchOnly).not.toHaveBeenCalled();
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
  });

  it("readers only refetch and never call the write endpoint", async () => {
    boundary.access.mockResolvedValue({ permissions: ["datasets.read"] });
    const refetchOnly = vi.fn();
    await render(refetchOnly);
    await vi.waitFor(() => expect(boundary.access).toHaveBeenCalled());

    await act(async () => probe.latest?.refresh());

    expect(refetchOnly).toHaveBeenCalledTimes(1);
    expect(boundary.refresh).not.toHaveBeenCalled();
    expect(probe.latest?.canPersist).toBe(false);
  });

  it("reports a failed persistence without invalidating the last information", async () => {
    boundary.access.mockResolvedValue({ permissions: ["control_room.write"] });
    boundary.refresh.mockRejectedValue(Object.assign(new Error("private"), { status: 429 }));
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    await render(vi.fn());
    await vi.waitFor(() => expect(probe.latest?.canPersist).toBe(true));

    await act(async () => probe.latest?.refresh());
    await vi.waitFor(() => expect(probe.latest?.failed).toBe(true));

    expect(invalidate).not.toHaveBeenCalled();
  });
});

describe("canPersistControlRoom", () => {
  it.each([
    [undefined, false],
    [[], false],
    [["datasets.read"], false],
    [["control_room.write"], true],
  ])("%j -> %s", (permissions, expected) => {
    expect(canPersistControlRoom(permissions)).toBe(expected);
  });
});
