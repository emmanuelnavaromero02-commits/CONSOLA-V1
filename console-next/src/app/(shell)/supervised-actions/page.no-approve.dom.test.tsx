// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SupervisedAction } from "@/lib/supervised-actions/types";

import SupervisedActionsPage from "./page";

// Boundary completo del client: si la página importara approve/execute,
// cualquier clic quedaría registrado aquí.
const clientBoundary = vi.hoisted(() => ({
  listSupervisedActions: vi.fn(),
  getSupervisedAction: vi.fn(),
  validateSupervisedAction: vi.fn(),
  approveSupervisedAction: vi.fn(),
  executeSupervisedAction: vi.fn(),
  rejectSupervisedAction: vi.fn(),
  cancelSupervisedAction: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@/lib/supervised-actions/client", () => clientBoundary);

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function makeAction(overrides: Partial<SupervisedAction> = {}): SupervisedAction {
  return {
    id: "act-1",
    title: "Actualizar puesto",
    status: "requires_approval",
    created_at: "2026-07-01T10:00:00Z",
    ...overrides,
  };
}

async function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <SupervisedActionsPage />
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  clientBoundary.listSupervisedActions.mockResolvedValue({ actions: [makeAction()] });
  clientBoundary.getSupervisedAction.mockResolvedValue(makeAction());
  clientBoundary.validateSupervisedAction.mockResolvedValue(makeAction({ status: "validated" }));
  clientBoundary.rejectSupervisedAction.mockResolvedValue(makeAction({ status: "cancelled" }));
  clientBoundary.cancelSupervisedAction.mockResolvedValue(makeAction({ status: "cancelled" }));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("SupervisedActionsPage sin camino a approve/execute (PR-A sin cablear)", () => {
  it("no renderiza CTA de Aprobar ni de Ejecutar, ni siquiera para requires_approval", async () => {
    await renderPage();

    const labels = [...container.querySelectorAll("button")].map(
      (button) => button.textContent?.trim() ?? "",
    );
    expect(labels).not.toContain("Aprobar");
    expect(labels).not.toContain("Ejecutar");
  });

  it("ningún clic ni submit alcanza approve o execute", async () => {
    await renderPage();

    // Clic sobre absolutamente todos los controles interactivos de la página.
    for (const button of [...container.querySelectorAll("button")]) {
      await act(async () => button.click());
    }
    for (const form of [...container.querySelectorAll("form")]) {
      await act(async () => {
        form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      });
    }

    expect(clientBoundary.approveSupervisedAction).not.toHaveBeenCalled();
    expect(clientBoundary.executeSupervisedAction).not.toHaveBeenCalled();
  });

  it("la activación por teclado tampoco alcanza approve o execute", async () => {
    await renderPage();

    for (const button of [...container.querySelectorAll("button")]) {
      await act(async () => {
        button.focus();
        button.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
        button.dispatchEvent(new KeyboardEvent("keyup", { key: " ", bubbles: true }));
      });
    }

    expect(clientBoundary.approveSupervisedAction).not.toHaveBeenCalled();
    expect(clientBoundary.executeSupervisedAction).not.toHaveBeenCalled();
  });

  it("el módulo de la página no referencia approve ni execute (contrato de superficie)", () => {
    const source = readFileSync(join(__dirname, "page.tsx"), "utf8");

    expect(source).not.toMatch(/approveSupervisedAction/);
    expect(source).not.toMatch(/executeSupervisedAction/);
    expect(source).not.toMatch(/\/approve/);
    expect(source).not.toMatch(/\/execute/);
  });
});
