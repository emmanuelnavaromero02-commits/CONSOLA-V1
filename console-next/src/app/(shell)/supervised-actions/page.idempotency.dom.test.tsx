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
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function findButton(label: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === label,
  );
}

function sentKeys(mock: ReturnType<typeof vi.fn>): string[] {
  return mock.mock.calls.map((call) => String(call[1]?.idempotency_key ?? ""));
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

describe("SupervisedActionsPage: idempotencia por intención lógica", () => {
  it("timeout ambiguo + reintento conserva exactamente la misma clave", async () => {
    clientBoundary.validateSupervisedAction
      .mockRejectedValueOnce(new Error("timeout"))
      .mockResolvedValueOnce(makeAction({ status: "validated" }));
    await renderPage();

    await act(async () => findButton("Validar")?.click());
    await act(async () => findButton("Validar")?.click());

    const keys = sentKeys(clientBoundary.validateSupervisedAction);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
  });

  it("éxito definitivo + nueva intención usa una clave diferente", async () => {
    await renderPage();

    await act(async () => findButton("Validar")?.click());
    await act(async () => findButton("Validar")?.click());

    const keys = sentKeys(clientBoundary.validateSupervisedAction);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBeTruthy();
    expect(keys[1]).not.toBe(keys[0]);
  });

  it("doble clic produce una sola solicitud activa", async () => {
    let resolveValidate: (value: SupervisedAction) => void = () => undefined;
    clientBoundary.validateSupervisedAction.mockImplementation(
      () => new Promise<SupervisedAction>((resolve) => {
        resolveValidate = resolve;
      }),
    );
    await renderPage();

    await act(async () => {
      const button = findButton("Validar");
      button?.click();
      button?.click();
    });

    expect(clientBoundary.validateSupervisedAction).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveValidate(makeAction({ status: "validated" }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  });

  it("intenciones distintas (operaciones distintas) no comparten clave", async () => {
    await renderPage();

    await act(async () => findButton("Validar")?.click());
    await act(async () => findButton("Rechazar")?.click());

    const validateKey = sentKeys(clientBoundary.validateSupervisedAction)[0];
    const rejectKey = sentKeys(clientBoundary.rejectSupervisedAction)[0];
    expect(validateKey).toBeTruthy();
    expect(rejectKey).toBeTruthy();
    expect(validateKey).not.toBe(rejectKey);
  });

  it("timeout ambiguo → desmontar → montar → retry: re-sincroniza estado y no hereda clave sin identidad durable", async () => {
    clientBoundary.validateSupervisedAction.mockRejectedValueOnce(new Error("timeout"));
    await renderPage();
    const listCallsBeforeError = clientBoundary.listSupervisedActions.mock.calls.length;

    await act(async () => findButton("Validar")?.click());
    const firstKey = sentKeys(clientBoundary.validateSupervisedAction)[0];
    expect(firstKey).toBeTruthy();

    expect(clientBoundary.listSupervisedActions.mock.calls.length).toBeGreaterThan(listCallsBeforeError);

    await act(async () => root.unmount());
    root = createRoot(container);
    await renderPage();
    await act(async () => findButton("Validar")?.click());

    const keys = sentKeys(clientBoundary.validateSupervisedAction);
    expect(keys).toHaveLength(2);
    expect(keys[1]).not.toBe(firstKey);
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("un remount no reutiliza la clave de una intención ya completada", async () => {
    await renderPage();
    await act(async () => findButton("Validar")?.click());
    const firstKey = sentKeys(clientBoundary.validateSupervisedAction)[0];
    expect(firstKey).toBeTruthy();

    await act(async () => root.unmount());
    root = createRoot(container);
    await renderPage();
    await act(async () => findButton("Validar")?.click());

    const keys = sentKeys(clientBoundary.validateSupervisedAction);
    expect(keys).toHaveLength(2);
    expect(keys[1]).not.toBe(firstKey);
  });
});
