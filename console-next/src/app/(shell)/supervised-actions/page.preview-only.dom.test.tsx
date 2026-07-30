// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SupervisedAction } from "@/lib/supervised-actions/types";

import SupervisedActionsPage from "./page";

const clientBoundary = vi.hoisted(() => ({
  listSupervisedActions: vi.fn(),
  getSupervisedAction: vi.fn(),
  validateSupervisedAction: vi.fn(),
  rejectSupervisedAction: vi.fn(),
  cancelSupervisedAction: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@/lib/supervised-actions/client", () => clientBoundary);

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MUTATION_LABELS = ["Validar", "Rechazar", "Cancelar"];

let container: HTMLDivElement;
let root: Root;

function makeAction(overrides: Partial<SupervisedAction> = {}): SupervisedAction {
  return {
    id: "act-1",
    title: "Actualizar puesto",
    status: "prepared",
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

describe("SupervisedActionsPage en modo preview-only", () => {
  it("no ofrece CTA de ejecución y muestra el aviso de solo preparación", async () => {
    clientBoundary.listSupervisedActions.mockResolvedValue({ actions: [makeAction()] });
    await renderPage();

    expect(findButton("Ejecutar")).toBeUndefined();
    expect(container.textContent).toContain(
      "La ejecución y la aprobación no están disponibles desde esta consola: las acciones operan en modo supervisado de solo preparación (preview).",
    );
    // Las mutaciones de preparación siguen disponibles sobre una acción activa.
    expect(findButton("Validar")?.disabled).toBe(false);
  });

  it.each(["executed", "cancelled"])(
    "deshabilita los CTAs de mutación para acciones en estado terminal %s",
    async (status) => {
      clientBoundary.listSupervisedActions.mockResolvedValue({
        actions: [makeAction({ id: `act-${status}`, status })],
      });
      await renderPage();

      for (const label of MUTATION_LABELS) {
        const button = findButton(label);
        expect(button, `botón ${label}`).toBeDefined();
        expect(button?.disabled, `botón ${label} deshabilitado`).toBe(true);
      }
    },
  );

  it("envía un idempotency_key UUID distinto por intención lógica", async () => {
    clientBoundary.listSupervisedActions.mockResolvedValue({ actions: [makeAction()] });
    await renderPage();

    await act(async () => findButton("Validar")?.click());
    await act(async () => findButton("Rechazar")?.click());

    expect(clientBoundary.validateSupervisedAction).toHaveBeenCalledWith("act-1", {
      idempotency_key: expect.stringMatching(UUID_PATTERN),
    });
    expect(clientBoundary.rejectSupervisedAction).toHaveBeenCalledWith("act-1", {
      idempotency_key: expect.stringMatching(UUID_PATTERN),
    });
    const validateKey = clientBoundary.validateSupervisedAction.mock.calls[0][1].idempotency_key;
    const rejectKey = clientBoundary.rejectSupervisedAction.mock.calls[0][1].idempotency_key;
    expect(validateKey).not.toBe(rejectKey);
  });

  it("una fuente ausente se declara no informada, nunca se inventa 'operativa'", async () => {
    clientBoundary.listSupervisedActions.mockResolvedValue({
      actions: [makeAction({ source_type: undefined, action_type: undefined })],
    });
    await renderPage();

    expect(container.textContent).toContain("Fuente no informada");
    expect(container.textContent).not.toContain("operativa");
    expect(container.textContent).toContain("Tipo no informado");
  });

  it("expone la carga de la cola con role=status", async () => {
    clientBoundary.listSupervisedActions.mockReturnValue(new Promise(() => {}));
    await renderPage();

    const status = container.querySelector('[role="status"]');
    expect(status).not.toBeNull();
    expect(status?.textContent).toContain("Cargando");
  });

  it("anuncia errores de carga con role=alert", async () => {
    clientBoundary.listSupervisedActions.mockRejectedValue(new Error("boom"));
    await renderPage();

    const alert = container.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert?.textContent).toContain("No se pudieron cargar las acciones.");
  });
});
