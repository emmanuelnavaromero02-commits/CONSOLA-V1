// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import OperationalIntelligencePage from "./page";

const queryState = vi.hoisted(() => ({
  current: {
    data: { items: [] },
    isLoading: false,
    isError: false,
    error: null,
    refetch: async () => undefined,
  },
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => queryState.current,
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

vi.mock("@/lib/operational-intelligence/client", () => ({
  createDecisionPlan: vi.fn(),
  getConfidenceHistory: vi.fn(),
  listDecisionPlans: vi.fn(),
  listHistoricalValidations: vi.fn(),
  listOperationalHistory: vi.fn(),
  listOperationalRuns: vi.fn(),
  listScenarioAnalyses: vi.fn(),
  runHistoricalValidation: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function tabs(): HTMLButtonElement[] {
  return [...container.querySelectorAll<HTMLButtonElement>('button[role="tab"]')];
}

function pressKey(target: Element, key: string) {
  target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
}

async function renderPage() {
  await act(async () => {
    root.render(<OperationalIntelligencePage />);
  });
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("Inteligencia Operativa: tablist accesible", () => {
  it("usa roving tabIndex: solo la pestaña activa es tabulable", async () => {
    await renderPage();

    const all = tabs();
    expect(all).toHaveLength(5);
    expect(all[0].tabIndex).toBe(0);
    for (const tab of all.slice(1)) expect(tab.tabIndex).toBe(-1);
  });

  it("cada tab está asociada a su panel con aria-controls y aria-labelledby", async () => {
    await renderPage();

    const active = tabs()[0];
    const controls = active.getAttribute("aria-controls");
    expect(controls).toBeTruthy();
    const panel = container.querySelector(`#${controls}`);
    expect(panel).not.toBeNull();
    expect(panel?.getAttribute("role")).toBe("tabpanel");
    expect(panel?.getAttribute("aria-labelledby")).toBe(active.id);
    expect(active.id).toBeTruthy();
  });

  it("flecha derecha mueve foco y selección a la siguiente pestaña (con ciclo)", async () => {
    await renderPage();

    const first = tabs()[0];
    first.focus();
    await act(async () => pressKey(first, "ArrowRight"));

    let all = tabs();
    expect(all[1].getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(all[1]);

    for (let i = 0; i < 4; i += 1) {
      await act(async () => pressKey(document.activeElement as Element, "ArrowRight"));
    }
    all = tabs();
    expect(all[0].getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(all[0]);
  });

  it("flecha izquierda desde la primera cicla a la última", async () => {
    await renderPage();

    const first = tabs()[0];
    first.focus();
    await act(async () => pressKey(first, "ArrowLeft"));

    const all = tabs();
    expect(all[4].getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(all[4]);
  });

  it("Home y End saltan a la primera y última pestaña", async () => {
    await renderPage();

    const first = tabs()[0];
    first.focus();
    await act(async () => pressKey(first, "End"));
    expect(tabs()[4].getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tabs()[4]);

    await act(async () => pressKey(document.activeElement as Element, "Home"));
    expect(tabs()[0].getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tabs()[0]);
  });
});
