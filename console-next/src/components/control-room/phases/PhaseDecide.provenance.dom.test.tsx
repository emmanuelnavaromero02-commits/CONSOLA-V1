// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PhaseDecide } from "./PhaseDecide";

interface MockQueryState {
  data: unknown;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => Promise<unknown>;
}

const queryState = vi.hoisted(() => ({
  current: {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    refetch: async () => undefined,
  } as MockQueryState,
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

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

async function renderPhase() {
  await act(async () => {
    root.render(<PhaseDecide />);
  });
}

beforeEach(() => {
  queryState.current = {
    data: {
      items: [
        {
          id: "row-1",
          simulation_id: "sim-1",
          title: "Escenario de demanda",
          status: "completed",
          created_at: "2026-07-01T10:00:00Z",
        },
      ],
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: async () => undefined,
  };
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("PhaseDecide: procedencia veraz", () => {
  it("los escenarios se rotulan como simulación, nunca como evidencia agregada", async () => {
    await renderPhase();

    expect(container.textContent).toContain("Simulación de escenarios");
    expect(container.textContent).not.toContain("evidencia agregada");
  });

  it("los planes no se rotulan como fuente operativa fabricada", async () => {
    await renderPhase();

    expect(container.textContent).not.toContain("fuente operativa");
    expect(container.textContent).toContain("Preparación supervisada");
  });

  it("la cabecera no presenta las simulaciones como datos observados", async () => {
    await renderPhase();

    expect(container.textContent).not.toContain(
      "validaciones conectadas a datos reales de la consola",
    );
  });

  it("validaciones sin result_count no fabrican '0 resultados'", async () => {
    await renderPhase();

    expect(container.textContent).not.toContain("0 resultados");
    expect(container.textContent).toContain("Resultados: N/D");
  });

  it("validaciones con result_count real sí muestran el conteo", async () => {
    queryState.current = {
      ...queryState.current,
      data: {
        items: [
          {
            id: "row-2",
            metric: "rotacion",
            status: "completed",
            created_at: "2026-07-01T10:00:00Z",
            result_count: 42,
          },
        ],
      },
    };
    await renderPhase();

    expect(container.textContent).toContain("42 resultados");
  });

  it("los paneles vacíos usan el estado vacío compartido sin inventar filas", async () => {
    queryState.current = { ...queryState.current, data: { items: [] } };
    await renderPhase();

    const empties = [...container.querySelectorAll('[data-testid="empty-state"]')].map(
      (node) => node.textContent,
    );
    expect(empties.length).toBeGreaterThan(0);
    expect(empties).toContain("Sin planes preparados.");
    expect(empties).toContain("Sin validaciones históricas.");
  });
});
