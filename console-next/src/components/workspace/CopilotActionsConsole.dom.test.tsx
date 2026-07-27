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
      isLoading: false,
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
