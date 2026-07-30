// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OperationWorkflow } from "@/lib/operations/types";

import OperationsWorkflowsPage from "./page";

const clientBoundary = vi.hoisted(() => ({
  listOperationWorkflows: vi.fn(),
  getOperationWorkflow: vi.fn(),
  planOperationWorkflow: vi.fn(),
  cancelOperationWorkflow: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@/lib/operations/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/operations/client")>();
  return { ...actual, ...clientBoundary };
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

function makeWorkflow(overrides: Partial<OperationWorkflow> = {}): OperationWorkflow {
  return {
    id: "wf-1",
    intent: "Preparar extracción",
    status: "created",
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
        <OperationsWorkflowsPage />
      </QueryClientProvider>,
    );
  });
  // Da tiempo a que react-query resuelva las queries iniciales.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function findButton(label: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === label,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  clientBoundary.getOperationWorkflow.mockResolvedValue({ workflow: makeWorkflow(), steps: [] });
  clientBoundary.planOperationWorkflow.mockResolvedValue({ ok: true, workflow_id: "wf-1" });
  clientBoundary.cancelOperationWorkflow.mockResolvedValue({ ok: true, workflow_id: "wf-1" });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("OperationsWorkflowsPage en modo preview-only", () => {
  it("ofrece Planificar en lugar de Ejecutar y muestra el aviso honesto", async () => {
    clientBoundary.listOperationWorkflows.mockResolvedValue([makeWorkflow()]);
    await renderPage();

    expect(findButton("Ejecutar")).toBeUndefined();
    const planButton = findButton("Planificar");
    expect(planButton).toBeDefined();
    expect(planButton?.disabled).toBe(false);
    expect(container.textContent).toContain(
      "La ejecución de workflows no está disponible desde esta consola: solo se permite planificar y revisar en modo preview.",
    );
  });

  it("Planificar llama solo al cliente de planificación, nunca al de ejecución", async () => {
    clientBoundary.listOperationWorkflows.mockResolvedValue([makeWorkflow()]);
    await renderPage();

    await act(async () => findButton("Planificar")?.click());

    expect(clientBoundary.planOperationWorkflow).toHaveBeenCalledTimes(1);
    expect(clientBoundary.planOperationWorkflow.mock.calls[0][0]).toMatchObject({ id: "wf-1" });
    expect(clientBoundary.cancelOperationWorkflow).not.toHaveBeenCalled();
  });

  it.each(["planning", "running", "waiting_approval", "completed", "cancelled", "failed"])(
    "deshabilita Planificar para workflows en estado %s",
    async (status) => {
      clientBoundary.listOperationWorkflows.mockResolvedValue([
        makeWorkflow({ id: `wf-${status}`, status }),
      ]);
      await renderPage();

      const planButton = findButton("Planificar");
      expect(planButton).toBeDefined();
      expect(planButton?.disabled).toBe(true);
    },
  );

  it("expone skeletons con role=status y métricas con aria-busy durante la carga", async () => {
    clientBoundary.listOperationWorkflows.mockReturnValue(new Promise(() => {}));
    await renderPage();

    expect(container.querySelector('[role="status"]')).not.toBeNull();
    const busyMetric = container.querySelector('[aria-busy="true"]');
    expect(busyMetric).not.toBeNull();
    expect(busyMetric?.textContent).toContain("Cargando");
  });

  it("anuncia errores de carga con role=alert", async () => {
    clientBoundary.listOperationWorkflows.mockRejectedValue(new Error("boom"));
    await renderPage();

    const alert = container.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert?.textContent).toContain("No se pudieron cargar workflows.");
  });
});
