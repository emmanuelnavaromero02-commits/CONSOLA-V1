// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ControlRoomPhases, resolveControlRoomPhase } from "./ControlRoomPhases";

const navigation = vi.hoisted(() => ({
  params: new URLSearchParams(),
}));

const accessBoundary = vi.hoisted(() => ({
  getMeAccess: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => navigation.params,
}));

vi.mock("@/lib/admin-surfaces", () => ({
  getMeAccess: accessBoundary.getMeAccess,
}));

vi.mock("./PhaseDecide", () => ({
  PhaseDecide: () => <div data-testid="phase-decide-stub">decide</div>,
}));
vi.mock("./PhaseEjecuta", () => ({
  PhaseEjecuta: () => <div data-testid="phase-ejecuta-stub">ejecuta</div>,
}));
vi.mock("./PhaseSupervisa", () => ({
  PhaseSupervisa: () => <div data-testid="phase-supervisa-stub">supervisa</div>,
}));
vi.mock("./PhaseEvoluciona", () => ({
  PhaseEvoluciona: () => <div data-testid="phase-evoluciona-stub">evoluciona</div>,
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

const FULL_ACCESS = {
  role: { is_platform_admin: false },
  permissions: ["datasets.read"],
  ui_capabilities: { can_view_decisions: true },
};

async function render(query: string) {
  navigation.params = new URLSearchParams(query);
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <ControlRoomPhases entiende={<div data-testid="entiende-stub">entiende</div>} />
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function tabs(): HTMLAnchorElement[] {
  return [...container.querySelectorAll<HTMLAnchorElement>('[role="tab"]')];
}

beforeEach(() => {
  vi.clearAllMocks();
  accessBoundary.getMeAccess.mockResolvedValue(FULL_ACCESS);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("ControlRoomPhases", () => {
  it("renders the five URL-driven tabs and defaults to Entiende", async () => {
    await render("");

    const all = tabs();
    expect(all.map((tab) => tab.textContent)).toEqual([
      "Entiende",
      "Decide",
      "Ejecuta",
      "Supervisa",
      "Evoluciona",
    ]);
    expect(all.map((tab) => tab.getAttribute("href"))).toEqual([
      "/control-room?fase=entiende",
      "/control-room?fase=decide",
      "/control-room?fase=ejecuta",
      "/control-room?fase=supervisa",
      "/control-room?fase=evoluciona",
    ]);
    expect(all[0].getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector('[data-testid="entiende-stub"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="phase-decide-stub"]')).toBeNull();
  });

  it("honours the fase deep link and mounts only that phase", async () => {
    await render("fase=supervisa");

    const selected = tabs().find((tab) => tab.getAttribute("aria-selected") === "true");
    expect(selected?.textContent).toBe("Supervisa");
    expect(container.querySelector('[data-testid="phase-supervisa-stub"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="entiende-stub"]')).toBeNull();
  });

  it("hides the Ejecuta tab without can_view_decisions and falls back on its deep link", async () => {
    accessBoundary.getMeAccess.mockResolvedValue({
      role: { is_platform_admin: false },
      permissions: ["datasets.read"],
      ui_capabilities: {},
    });
    await render("fase=ejecuta");

    expect(tabs().map((tab) => tab.textContent)).toEqual([
      "Entiende",
      "Decide",
      "Supervisa",
      "Evoluciona",
    ]);
    expect(container.querySelector('[data-testid="phase-ejecuta-stub"]')).toBeNull();
    expect(container.querySelector('[data-testid="entiende-stub"]')).not.toBeNull();
  });

  it("hides the tablist entirely when only one phase is visible", async () => {
    accessBoundary.getMeAccess.mockResolvedValue({
      role: { is_platform_admin: false },
      permissions: [],
      ui_capabilities: { can_view_decisions: true },
    });
    await render("");

    expect(container.querySelector('[role="tablist"]')).toBeNull();
    expect(container.querySelector('[data-testid="phase-ejecuta-stub"]')).not.toBeNull();
  });

  it("renders a retryable error state when the access read fails", async () => {
    accessBoundary.getMeAccess.mockRejectedValueOnce(new Error("boom"));
    await render("fase=decide");

    expect(container.querySelector('[role="tablist"]')).toBeNull();
    expect(container.querySelector('[data-testid="phase-decide-stub"]')).toBeNull();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "No se pudo cargar tu acceso.",
    );

    accessBoundary.getMeAccess.mockResolvedValue(FULL_ACCESS);
    const retry = [...container.querySelectorAll("button")].find(
      (button) => button.textContent?.trim() === "Reintentar",
    );
    expect(retry).toBeDefined();
    await act(async () => retry?.click());
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(container.querySelector('[data-testid="phase-decide-stub"]')).not.toBeNull();
  });

  it("uses roving tabIndex and exposes the accessible tablist contract", async () => {
    await render("fase=decide");

    const all = tabs();
    const selectedIndexes = all.map((tab) => tab.tabIndex);
    expect(selectedIndexes.filter((value) => value === 0)).toHaveLength(1);
    expect(container.querySelector('[role="tablist"]')?.getAttribute("aria-label")).toBe(
      "Ciclo operativo",
    );
    const panel = container.querySelector('[role="tabpanel"]');
    expect(panel?.getAttribute("aria-labelledby")).toBe("cr-phase-tab-decide");
  });

  it("parses only known fases and defaults to entiende", () => {
    expect(resolveControlRoomPhase(null)).toBe("entiende");
    expect(resolveControlRoomPhase("otro")).toBe("entiende");
    expect(resolveControlRoomPhase("evoluciona")).toBe("evoluciona");
  });
});
