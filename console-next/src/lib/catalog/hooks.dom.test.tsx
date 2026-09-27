// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AutoProfileStatus } from "@/lib/data/types";

const client = vi.hoisted(() => ({ autoProfileCatalog: vi.fn() }));

vi.mock("@/lib/data/client", () => client);

import {
  AUTO_CATALOG_DEBOUNCE_MS,
  AUTO_CATALOG_MAX_IDLE_POLLS,
  AUTO_CATALOG_MAX_POLL_MS,
  AUTO_CATALOG_MAX_WAIT_MS,
  AUTO_CATALOG_POLL_MS,
  useAutoCatalog,
} from "./hooks";

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;

function Probe({ cartridge, includeSources }: { cartridge?: string | null; includeSources?: boolean }) {
  const status = useAutoCatalog({ cartridge, includeSources });
  return <output>{status ? JSON.stringify(status) : ""}</output>;
}

function latest(): AutoProfileStatus | null {
  const text = container.querySelector("output")?.textContent ?? "";
  return text ? (JSON.parse(text) as AutoProfileStatus) : null;
}

async function render(node: React.ReactNode) {
  await act(async () => {
    root.render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
  });
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

function status(partial: Partial<AutoProfileStatus>): AutoProfileStatus {
  return { status: "idle", processed: 0, pending: 0, stale: 0, annotation_epoch: null, ...partial };
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  client.autoProfileCatalog.mockReset();
  queryClient = new QueryClient();
  vi.spyOn(queryClient, "invalidateQueries");
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("useAutoCatalog", () => {
  it("fires once after the debounce and invalidates the catalog on progress", async () => {
    client.autoProfileCatalog.mockResolvedValue(status({ status: "ready", processed: 2, stale: 2 }));
    await render(<Probe cartridge="sap_successfactors" />);
    await advance(AUTO_CATALOG_DEBOUNCE_MS - 1);
    expect(client.autoProfileCatalog).not.toHaveBeenCalled();
    await advance(1);
    expect(client.autoProfileCatalog).toHaveBeenCalledTimes(1);
    expect(client.autoProfileCatalog).toHaveBeenCalledWith({ cartridge: "sap_successfactors", include_sources: false });
    expect(queryClient.invalidateQueries).toHaveBeenCalledWith({ queryKey: ["data", "catalog"] });
    expect(latest()?.processed).toBe(2);
    await render(<Probe cartridge="sap_successfactors" />);
    await advance(10_000);
    expect(client.autoProfileCatalog).toHaveBeenCalledTimes(1);
  });

  it("follows the background work until nothing is pending, refreshing on every new epoch", async () => {
    const polls = [
      status({ status: "working", pending: 9, stale: 9, annotation_epoch: "e0" }),
      ...Array.from({ length: 9 }, (_, index) =>
        status({
          status: index === 8 ? "ready" : "working",
          pending: 8 - index,
          processed: index + 1,
          annotation_epoch: `e${index + 1}`,
        }),
      ),
    ];
    for (const poll of polls) client.autoProfileCatalog.mockResolvedValueOnce(poll);
    await render(<Probe cartridge="sap_successfactors" />);
    await advance(AUTO_CATALOG_DEBOUNCE_MS);
    await advance(AUTO_CATALOG_MAX_WAIT_MS);
    expect(client.autoProfileCatalog).toHaveBeenCalledTimes(10);
    expect(client.autoProfileCatalog.mock.calls[0][0]).toEqual({
      cartridge: "sap_successfactors",
      include_sources: false,
      since: undefined,
    });
    for (const call of client.autoProfileCatalog.mock.calls.slice(1)) {
      expect(call[0].since).toBe("e0");
    }
    expect(queryClient.invalidateQueries).toHaveBeenCalledTimes(9);
    expect(latest()?.processed).toBe(9);
    expect(latest()?.pending).toBe(0);
  });

  it("backs off between polls up to the cap", async () => {
    client.autoProfileCatalog.mockImplementation(async () => {
      const call = client.autoProfileCatalog.mock.calls.length;
      return status({ status: "working", pending: 50 - call, annotation_epoch: `e${call}` });
    });
    await render(<Probe />);
    await advance(AUTO_CATALOG_DEBOUNCE_MS);
    expect(client.autoProfileCatalog).toHaveBeenCalledTimes(1);
    await advance(AUTO_CATALOG_POLL_MS);
    expect(client.autoProfileCatalog).toHaveBeenCalledTimes(2);
    await advance(AUTO_CATALOG_POLL_MS);
    expect(client.autoProfileCatalog).toHaveBeenCalledTimes(2);
    await advance(AUTO_CATALOG_POLL_MS * 1.5 - AUTO_CATALOG_POLL_MS);
    expect(client.autoProfileCatalog).toHaveBeenCalledTimes(3);
    // The poll that starts just before the three-minute limit may still
    // schedule one last follow-up; after that the hook stops for good.
    await advance(AUTO_CATALOG_MAX_WAIT_MS + AUTO_CATALOG_MAX_POLL_MS);
    const calls = client.autoProfileCatalog.mock.calls.length;
    expect(calls).toBeLessThan(40);
    await advance(AUTO_CATALOG_MAX_POLL_MS * 4);
    expect(client.autoProfileCatalog.mock.calls.length).toBe(calls);
  });

  it("gives up after several polls without progress", async () => {
    client.autoProfileCatalog.mockResolvedValue(
      status({ status: "working", pending: 3, stale: 3, annotation_epoch: "same" }),
    );
    await render(<Probe />);
    await advance(AUTO_CATALOG_DEBOUNCE_MS);
    await advance(AUTO_CATALOG_MAX_WAIT_MS);
    expect(client.autoProfileCatalog).toHaveBeenCalledTimes(AUTO_CATALOG_MAX_IDLE_POLLS);
    expect(queryClient.invalidateQueries).not.toHaveBeenCalled();
  });

  it("waits while the tab is hidden and fires when it becomes visible", async () => {
    let hidden = true;
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
    client.autoProfileCatalog.mockResolvedValue(status({}));
    await render(<Probe includeSources />);
    await advance(AUTO_CATALOG_DEBOUNCE_MS * 3);
    expect(client.autoProfileCatalog).not.toHaveBeenCalled();
    hidden = false;
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(client.autoProfileCatalog).toHaveBeenCalledWith({ cartridge: undefined, include_sources: true });
  });

  it("swallows errors silently", async () => {
    client.autoProfileCatalog.mockRejectedValue(new Error("503"));
    await render(<Probe />);
    await advance(AUTO_CATALOG_DEBOUNCE_MS);
    expect(latest()).toBeNull();
    expect(queryClient.invalidateQueries).not.toHaveBeenCalled();
  });

  it("runs again when the data source filter changes", async () => {
    client.autoProfileCatalog.mockResolvedValue(status({}));
    await render(<Probe cartridge="a" />);
    await advance(AUTO_CATALOG_DEBOUNCE_MS);
    await render(<Probe cartridge="b" />);
    await advance(AUTO_CATALOG_DEBOUNCE_MS);
    expect(client.autoProfileCatalog.mock.calls.map((call) => call[0].cartridge)).toEqual(["a", "b"]);
  });
});
