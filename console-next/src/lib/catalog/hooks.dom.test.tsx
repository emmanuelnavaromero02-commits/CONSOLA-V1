// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AutoProfileStatus } from "@/lib/data/types";

const client = vi.hoisted(() => ({ autoProfileCatalog: vi.fn() }));

vi.mock("@/lib/data/client", () => client);

import { AUTO_CATALOG_DEBOUNCE_MS, AUTO_CATALOG_POLL_MS, useAutoCatalog } from "./hooks";

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

  it("re-polls every two seconds while work is pending, at most five times", async () => {
    client.autoProfileCatalog.mockResolvedValue(status({ status: "working", pending: 3, stale: 3 }));
    await render(<Probe />);
    await advance(AUTO_CATALOG_DEBOUNCE_MS);
    for (let index = 0; index < 8; index += 1) {
      await advance(AUTO_CATALOG_POLL_MS);
    }
    expect(client.autoProfileCatalog).toHaveBeenCalledTimes(6);
    expect(client.autoProfileCatalog).toHaveBeenCalledWith({ cartridge: undefined, include_sources: false });
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
