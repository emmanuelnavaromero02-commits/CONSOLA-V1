// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Decision } from "@/lib/admin-surfaces";

import { DecisionsBoard } from "./DecisionsBoard";

const boundary = vi.hoisted(() => ({
  listDecisions: vi.fn(),
  getDecision: vi.fn(),
  updateDecision: vi.fn(),
  createDecision: vi.fn(),
  deleteDecision: vi.fn(),
  addDecisionAction: vi.fn(),
}));

vi.mock("@/lib/admin-surfaces", () => boundary);

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

const DECISIONS: Decision[] = [
  { id: 1, title: "Reponer lote de la sucursal norte", status: "open", outcome: null, visibility: "shared" },
  { id: 2, title: "Renegociar margen con distribuidor", status: "closed", outcome: "not_achieved", visibility: "shared" },
  { id: 3, title: "Liquidar lote por caducar", status: "closed", outcome: "achieved", visibility: "private" },
];

async function flush() {
  for (let index = 0; index < 4; index += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function render() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <DecisionsBoard />
      </QueryClientProvider>,
    );
  });
  await flush();
}

function button(label: string) {
  return [...container.querySelectorAll("button")].find((node) => node.textContent?.trim() === label);
}

async function click(node: Element | undefined) {
  expect(node).toBeDefined();
  await act(async () => (node as HTMLElement).click());
  await flush();
}

function detailValue(label: string) {
  const term = [...container.querySelectorAll("aside dt")].find((node) => node.textContent === label);
  return term?.nextElementSibling?.textContent;
}

beforeEach(() => {
  vi.clearAllMocks();
  boundary.listDecisions.mockResolvedValue(DECISIONS);
  boundary.getDecision.mockImplementation(async (id: number) => ({ ...DECISIONS.find((row) => row.id === id), actions: [] }));
  boundary.updateDecision.mockImplementation(async (id: number, payload: Partial<Decision>) => ({
    ...DECISIONS.find((row) => row.id === id),
    ...payload,
  }));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("DecisionsBoard", () => {
  it("shows the outcome of each decision in Spanish", async () => {
    await render();
    const outcomes = [...container.querySelectorAll("tbody tr")].map((row) => row.lastElementChild?.textContent);
    expect(outcomes).toEqual(["—", "No lograda", "Lograda"]);

    await click(button("Renegociar margen con distribuidor"));
    expect(detailValue("Resultado")).toBe("No lograda");
  });

  it("closes an open decision as achieved or not achieved", async () => {
    await render();
    await click(button("Reponer lote de la sucursal norte"));
    expect(detailValue("Resultado")).toBe("—");
    expect(button("Cerrar como lograda")).toBeDefined();

    await click(button("Cerrar como no lograda"));
    expect(boundary.updateDecision).toHaveBeenCalledWith(1, { status: "closed", outcome: "not_achieved" });

    await click(button("Cerrar como lograda"));
    expect(boundary.updateDecision).toHaveBeenLastCalledWith(1, { status: "closed", outcome: "achieved" });
  });

  it("reopens a closed decision and clears its outcome", async () => {
    await render();
    await click(button("Liquidar lote por caducar"));
    expect(button("Cerrar como lograda")).toBeUndefined();
    expect(button("Cerrar como no lograda")).toBeUndefined();

    await click(button("Reabrir"));
    expect(boundary.updateDecision).toHaveBeenCalledWith(3, { status: "open", outcome: null });
  });
});
