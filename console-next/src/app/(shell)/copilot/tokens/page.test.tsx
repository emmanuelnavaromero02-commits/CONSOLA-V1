import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CopilotTokensPage from "./page";

interface MockQueryState {
  data: unknown;
  isLoading: boolean;
  isError: boolean;
  isFetching: boolean;
  refetch: () => void;
}

const queryState = vi.hoisted(() => ({
  current: {
    data: undefined,
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: () => undefined,
  } as MockQueryState,
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => queryState.current,
}));

vi.mock("@/components/copilot/WorkspaceTokenKeys", () => ({
  WorkspaceTokenKeys: () => null,
}));

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn() },
}));

const FULL_SUMMARY = {
  available: true,
  input_tokens: 100,
  output_tokens: 50,
  cache_creation_tokens: 10,
  cache_read_tokens: 5,
  calls: 7,
  cost_usd: 1.5,
  models: [
    {
      model: "claude-haiku-4-5-20251001",
      input_tokens: 100,
      output_tokens: 50,
      cache_creation_tokens: 10,
      cache_read_tokens: 5,
      calls: 5,
      cost_usd: 1.5,
      priced: true,
    },
    {
      model: "mystery-model",
      input_tokens: 9,
      output_tokens: 9,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
      calls: 2,
      cost_usd: null,
      priced: false,
    },
  ],
  unpriced_models: ["mystery-model"],
  avg_response_ms: 1830.4,
  queries_count: 4,
};

beforeEach(() => {
  queryState.current = {
    data: undefined,
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: () => undefined,
  };
});

describe("CopilotTokensPage", () => {
  it("presents investment and usage in business terms", () => {
    queryState.current.data = FULL_SUMMARY;

    const markup = renderToStaticMarkup(<CopilotTokensPage />);

    expect(markup).toContain("Inversión y Uso del Asistente");
    expect(markup).toContain("Consultas realizadas");
    expect(markup).toContain("Tiempo de respuesta promedio");
    expect(markup).toContain("Inversión acumulada (USD)");
    expect(markup).toContain("$1.50");
    expect(markup).toContain("1,8 s");
    expect(markup).toContain("Avanzado");
    expect(markup).toContain("No incluye modelos sin precio configurado");
    expect(markup).toContain("Sin precio configurado");
    expect(markup).toContain("mystery-model");
  });

  it("shows Sin información instead of zeros when observability fields are null", () => {
    queryState.current.data = {
      ...FULL_SUMMARY,
      models: [],
      unpriced_models: [],
      avg_response_ms: null,
      queries_count: null,
    };

    const markup = renderToStaticMarkup(<CopilotTokensPage />);

    expect(markup).toContain("Sin información");
    expect(markup).not.toContain('font-semibold">0<');
  });

  it("shows Sin información everywhere when the summary is unavailable", () => {
    queryState.current.data = {
      available: false,
      input_tokens: null,
      output_tokens: null,
      cache_creation_tokens: null,
      cache_read_tokens: null,
      calls: null,
      cost_usd: null,
      models: [],
      unpriced_models: [],
      avg_response_ms: null,
      queries_count: null,
    };

    const markup = renderToStaticMarkup(<CopilotTokensPage />);

    expect(markup).toContain("La información de uso no está disponible en este momento.");
    expect(markup).toContain("Sin información");
    expect(markup).toContain("No hay datos disponibles.");
    expect(markup).toContain("— modelos");
    expect(markup).not.toContain("$0.00");
    expect(markup).not.toContain('font-semibold">0<');
  });

  it("shows — instead of fabricated $0.00/0 metrics when the request failed", () => {
    queryState.current.isError = true;

    const markup = renderToStaticMarkup(<CopilotTokensPage />);

    expect(markup).toContain("—");
    expect(markup).not.toContain("$0.00");
    expect(markup).not.toContain('font-semibold">0<');
    expect(markup).toContain("— modelos");
    expect(markup).toContain("No se pudo cargar la información de uso.");
    expect(markup).toContain('role="alert"');
    expect(markup).toContain("No hay datos disponibles.");
  });

  it("marks the loading placeholder rows as busy status", () => {
    queryState.current.isLoading = true;

    const markup = renderToStaticMarkup(<CopilotTokensPage />);

    expect(markup).toContain('role="status"');
    expect(markup).toContain('aria-busy="true"');
  });
});
