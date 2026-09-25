// @vitest-environment jsdom

import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getCopilotContextSnapshot } from "@/lib/copilot/client";

import { CopilotActionsConsole } from "./CopilotActionsConsole";


const clientBoundary = vi.hoisted(() => ({
  getCopilotContextSnapshot: vi.fn(async () => ({ status: "ready", sources: [] })),
}));
const snapshotEndpoint = vi.mocked(getCopilotContextSnapshot);

let accessPermissions: string[] = [];
let queriesLoading = false;
let container: HTMLDivElement;
let root: Root;

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@/lib/copilot/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/copilot/client")>();
  return {
    ...actual,
    getCopilotContextSnapshot: clientBoundary.getCopilotContextSnapshot,
  };
});

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
  useQuery: ({ queryKey, queryFn }: { queryKey: string[]; queryFn: () => unknown }) => {
    const name = queryKey.join(":");
    if (name === "me:access") {
      return {
        data: { permissions: accessPermissions },
        isLoading: false,
        refetch: vi.fn(),
      };
    }
    return {
      data: name.includes("recommendations") ? [] : undefined,
      isLoading: queriesLoading,
      refetch: name === "copilot:actions:live-context" ? queryFn : vi.fn(),
    };
  },
}));

async function renderAndClickRefresh(permissions: string[]) {
  accessPermissions = permissions;
  await act(async () => root.render(<CopilotActionsConsole />));
  const button = [...container.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === "Actualizar",
  );
  expect(button).toBeDefined();
  await act(async () => button?.click());
}

beforeEach(() => {
  snapshotEndpoint.mockClear();
  queriesLoading = false;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("CopilotActionsConsole refresh interaction", () => {
  it("does not call the snapshot endpoint when a viewer clicks Actualizar", async () => {
    await renderAndClickRefresh([]);

    expect(snapshotEndpoint).not.toHaveBeenCalled();
  });

  it("calls the snapshot endpoint once when an authorized operator clicks Actualizar", async () => {
    await renderAndClickRefresh(["operations.read"]);

    expect(snapshotEndpoint).toHaveBeenCalledTimes(1);
  });
});

describe("CopilotActionsConsole loading accessibility", () => {
  it("announces loading skeletons via role=status, sr-only text and aria-busy sections", async () => {
    accessPermissions = ["operations.read"];
    queriesLoading = true;
    await act(async () => root.render(<CopilotActionsConsole />));

    const statuses = [...container.querySelectorAll('[role="status"]')];
    expect(statuses.length).toBeGreaterThan(0);
    for (const status of statuses) {
      expect(status.querySelector(".sr-only")?.textContent).toBe("Cargando");
    }

    const busySections = [...container.querySelectorAll('section[aria-busy="true"]')];
    expect(busySections.length).toBeGreaterThan(0);

    const hiddenDots = [...container.querySelectorAll('span[aria-hidden="true"]')].filter(
      (node) => node.textContent === "...",
    );
    expect(hiddenDots.length).toBeGreaterThan(0);
    for (const dots of hiddenDots) {
      expect(dots.parentElement?.querySelector(".sr-only")?.textContent).toBe("Cargando");
    }
  });

  it("keeps sections not busy and skeleton-free once data resolves", async () => {
    accessPermissions = ["operations.read"];
    queriesLoading = false;
    await act(async () => root.render(<CopilotActionsConsole />));

    expect(container.querySelector('section[aria-busy="true"]')).toBeNull();
    expect(container.querySelector(".animate-pulse")).toBeNull();
  });
});
