// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PhaseSupervisa, decisionImpact, remainingDays } from "./PhaseSupervisa";

const decisionsBoundary = vi.hoisted(() => ({
  listDecisions: vi.fn(),
  updateDecision: vi.fn(),
  addDecisionAction: vi.fn(),
  getMeAccess: vi.fn(),
}));

const actionsBoundary = vi.hoisted(() => ({
  listSupervisedActions: vi.fn(),
  getSupervisedAction: vi.fn(),
  validateSupervisedAction: vi.fn(),
  approveSupervisedAction: vi.fn(),
  executeSupervisedAction: vi.fn(),
  rejectSupervisedAction: vi.fn(),
  cancelSupervisedAction: vi.fn(),
}));

vi.mock("@/lib/admin-surfaces", () => ({
  listDecisions: decisionsBoundary.listDecisions,
  updateDecision: decisionsBoundary.updateDecision,
  addDecisionAction: decisionsBoundary.addDecisionAction,
  getMeAccess: decisionsBoundary.getMeAccess,
}));

vi.mock("@/lib/supervised-actions/client", () => actionsBoundary);

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const NOW = new Date(2026, 6, 15);
const READER_ACCESS = { permissions: ["datasets.read"], ui_capabilities: {} };
const WRITER_ACCESS = {
  permissions: ["datasets.read", "control_room.write"],
  ui_capabilities: {},
};

let container: HTMLDivElement;
let root: Root;

async function renderPhase() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <PhaseSupervisa />
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

function setTextareaValue(value: string) {
  const textarea = container.querySelector("textarea");
  expect(textarea).not.toBeNull();
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    "value",
  )?.set;
  setter?.call(textarea, value);
  textarea?.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  vi.clearAllMocks();
  decisionsBoundary.getMeAccess.mockResolvedValue(READER_ACCESS);
  decisionsBoundary.listDecisions.mockResolvedValue([]);
  decisionsBoundary.updateDecision.mockResolvedValue({ id: 1, status: "closed" });
  decisionsBoundary.addDecisionAction.mockResolvedValue({ id: 9 });
  actionsBoundary.listSupervisedActions.mockResolvedValue({ actions: [] });
  actionsBoundary.getSupervisedAction.mockResolvedValue({ id: "act-1" });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("remainingDays", () => {
  it("labels future commitments with the remaining days", () => {
    expect(remainingDays("2026-07-20", NOW)).toEqual({
      label: "5 días restantes",
      overdue: false,
    });
    expect(remainingDays("2026-07-16", NOW)).toEqual({
      label: "1 día restante",
      overdue: false,
    });
  });

  it("labels today's commitment and past ones as overdue", () => {
    expect(remainingDays("2026-07-15", NOW)).toEqual({ label: "Vence hoy", overdue: false });
    expect(remainingDays("2026-07-14", NOW)).toEqual({
      label: "Vencido hace 1 día",
      overdue: true,
    });
    expect(remainingDays("2026-07-01", NOW)).toEqual({
      label: "Vencido hace 14 días",
      overdue: true,
    });
  });

  it("never invents a date when the commitment is NULL or invalid", () => {
    expect(remainingDays(null, NOW)).toEqual({ label: "Sin fecha compromiso", overdue: false });
    expect(remainingDays(undefined, NOW)).toEqual({
      label: "Sin fecha compromiso",
      overdue: false,
    });
    expect(remainingDays("no-date", NOW)).toEqual({
      label: "Sin fecha compromiso",
      overdue: false,
    });
  });
});

describe("decisionImpact", () => {
  it("reads the persisted snapshot with its Spanish rule", () => {
    const impact = decisionImpact([
      { label: "Severidad", value: "high" },
      {
        impacto_estimado: { valor: 1500, moneda: "USD" },
        regla: "Regla: costo mensual directo",
      },
    ]);

    expect(impact).not.toBeNull();
    expect(impact?.text).toContain("1,500");
    expect(impact?.text).toContain("USD");
    expect(impact?.rule).toBe("Regla: costo mensual directo");
  });

  it("returns null for legacy rows without the snapshot", () => {
    expect(decisionImpact([{ label: "Severidad", value: "high" }])).toBeNull();
    expect(decisionImpact(undefined)).toBeNull();
    expect(decisionImpact("kpis")).toBeNull();
    expect(decisionImpact([{ impacto_estimado: { valor: "alto" } }])).toBeNull();
  });
});

describe("PhaseSupervisa", () => {
  it("renders open tickets with responsible, remaining days and impact", async () => {
    decisionsBoundary.listDecisions.mockResolvedValue([
      {
        id: 1,
        title: "Regularizar backlog",
        commitment_date: "2000-01-05",
        created_by: "sistema-omega",
        kpis: [
          {
            impacto_estimado: { valor: 900, moneda: "USD" },
            regla: "Regla: valor abierto pendiente",
          },
        ],
      },
      {
        id: 2,
        title: "Decisión heredada",
        commitment_date: null,
        created_by: null,
        kpis: [],
      },
    ]);
    await renderPhase();

    expect(decisionsBoundary.listDecisions).toHaveBeenCalledWith("open");
    expect(container.textContent).toContain("Tickets en curso");
    expect(container.textContent).toContain("Regularizar backlog");
    expect(container.textContent).toContain("sistema-omega");
    expect(container.textContent).toContain("Vencido hace");
    expect(container.textContent).toContain("Regla: valor abierto pendiente");
    expect(container.textContent).toContain("Sin fecha compromiso");
    expect(container.textContent).toContain("Sin información");
  });

  it("shows the shared empty state without inventing tickets", async () => {
    await renderPhase();

    expect(container.textContent).toContain("Sin tickets en curso.");
  });

  it("hides the ticket actions without control_room.write", async () => {
    decisionsBoundary.listDecisions.mockResolvedValue([
      { id: 1, title: "Regularizar backlog", commitment_date: null, kpis: [] },
    ]);
    await renderPhase();

    expect(findButton("Cerrar con resultado")).toBeUndefined();
    expect(findButton("Reabrir")).toBeUndefined();
    const headers = [...container.querySelectorAll("th")].map((th) => th.textContent?.trim());
    expect(headers).not.toContain("Acciones");
  });

  it("closes a ticket with an explicit outcome and optional note", async () => {
    decisionsBoundary.getMeAccess.mockResolvedValue(WRITER_ACCESS);
    decisionsBoundary.listDecisions.mockResolvedValue([
      { id: 7, title: "Regularizar backlog", commitment_date: null, kpis: [] },
    ]);
    await renderPhase();

    await act(async () => findButton("Cerrar con resultado")?.click());
    const dialog = container.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
    expect(dialog?.textContent).toContain("Cumplida");
    expect(dialog?.textContent).toContain("No cumplida");

    const confirm = findButton("Confirmar cierre");
    expect(confirm?.disabled).toBe(true);

    const radio = [...container.querySelectorAll<HTMLInputElement>('input[type="radio"]')]
      .find((input) => input.value === "achieved");
    await act(async () => radio?.click());
    setTextareaValue("Cerrada tras revisar el backlog.");
    expect(findButton("Confirmar cierre")?.disabled).toBe(false);

    await act(async () => findButton("Confirmar cierre")?.click());

    expect(decisionsBoundary.updateDecision).toHaveBeenCalledWith(7, {
      status: "closed",
      outcome: "achieved",
    });
    expect(decisionsBoundary.addDecisionAction).toHaveBeenCalledWith(
      7,
      "Cerrada tras revisar el backlog.",
    );
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      "Decisión cerrada.",
    );
  });

  it("reopens a closed ticket after confirmation", async () => {
    decisionsBoundary.getMeAccess.mockResolvedValue(WRITER_ACCESS);
    decisionsBoundary.listDecisions.mockImplementation(async (status: string) =>
      status === "closed"
        ? [{ id: 4, title: "Decisión cerrada", commitment_date: null, outcome: "not_achieved", kpis: [] }]
        : [],
    );
    await renderPhase();

    await act(async () => findButton("Cerrados")?.click());
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(container.textContent).toContain("No cumplida");

    await act(async () => findButton("Reabrir")?.click());
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
    await act(async () => findButton("Confirmar reapertura")?.click());

    expect(decisionsBoundary.updateDecision).toHaveBeenCalledWith(4, {
      status: "open",
      outcome: null,
    });
    expect(decisionsBoundary.addDecisionAction).not.toHaveBeenCalled();
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      "Decisión reabierta.",
    );
  });

  it("surfaces a partial-note close honestly", async () => {
    decisionsBoundary.getMeAccess.mockResolvedValue(WRITER_ACCESS);
    decisionsBoundary.listDecisions.mockResolvedValue([
      { id: 7, title: "Regularizar backlog", commitment_date: null, kpis: [] },
    ]);
    decisionsBoundary.addDecisionAction.mockRejectedValue(new Error("boom"));
    await renderPhase();

    await act(async () => findButton("Cerrar con resultado")?.click());
    const radio = [...container.querySelectorAll<HTMLInputElement>('input[type="radio"]')]
      .find((input) => input.value === "not_achieved");
    await act(async () => radio?.click());
    setTextareaValue("nota");
    await act(async () => findButton("Confirmar cierre")?.click());

    expect(decisionsBoundary.updateDecision).toHaveBeenCalledWith(7, {
      status: "closed",
      outcome: "not_achieved",
    });
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "La decisión se cerró, pero la nota no se pudo registrar.",
    );
  });

  it("keeps the #551 invariant: no approve/execute path anywhere in the phase", async () => {
    actionsBoundary.listSupervisedActions.mockResolvedValue({
      actions: [
        {
          id: "act-1",
          title: "Actualizar puesto",
          status: "requires_approval",
          created_at: "2026-07-01T10:00:00Z",
        },
      ],
    });
    await renderPhase();

    const labels = [...container.querySelectorAll("button")].map(
      (button) => button.textContent?.trim() ?? "",
    );
    expect(labels).not.toContain("Aprobar");
    expect(labels).not.toContain("Ejecutar");

    for (const button of [...container.querySelectorAll("button")]) {
      await act(async () => button.click());
    }
    expect(actionsBoundary.approveSupervisedAction).not.toHaveBeenCalled();
    expect(actionsBoundary.executeSupervisedAction).not.toHaveBeenCalled();
  });

  it("declares the supervised queue as permission-gated instead of alerting on 403", async () => {
    const forbidden = Object.assign(new Error("forbidden"), { status: 403 });
    actionsBoundary.listSupervisedActions.mockRejectedValue(forbidden);
    await renderPhase();

    expect(container.textContent).toContain(
      "Requiere permisos de operación para ver la cola supervisada.",
    );
    expect(container.textContent).not.toContain("No se pudieron cargar las acciones.");
  });
});
