import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import OperationalIntelligencePage from "./page";

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
    isLoading: true,
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

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

beforeEach(() => {
  queryState.current = {
    data: undefined,
    isLoading: true,
    isError: false,
    error: null,
    refetch: async () => undefined,
  };
});

describe("OperationalIntelligencePage summary tiles", () => {
  it("shows — instead of 0 while queries are loading", () => {
    const markup = renderToStaticMarkup(<OperationalIntelligencePage />);

    expect(markup).toContain('font-semibold">—<');
    expect(markup).not.toContain('font-semibold">0<');
  });

  it("shows — instead of 0 when queries failed without data", () => {
    queryState.current = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("backend down"),
      refetch: async () => undefined,
    };

    const markup = renderToStaticMarkup(<OperationalIntelligencePage />);

    expect(markup).toContain('font-semibold">—<');
    expect(markup).not.toContain('font-semibold">0<');
    expect(markup).toContain('role="alert"');
    expect(markup).toContain("No se pudo cargar la información.");
  });

  it("shows real counts once data arrives", () => {
    queryState.current = {
      data: { items: [{ id: "a" }, { id: "b" }] },
      isLoading: false,
      isError: false,
      error: null,
      refetch: async () => undefined,
    };

    const markup = renderToStaticMarkup(<OperationalIntelligencePage />);

    expect(markup).toContain('font-semibold">2<');
    expect(markup).not.toContain('font-semibold">—<');
  });
});
