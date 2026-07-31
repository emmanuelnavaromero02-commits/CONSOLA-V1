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
  it("shows — instead of fabricated $0.00/0 metrics when the summary failed", () => {
    queryState.current.isError = true;

    const markup = renderToStaticMarkup(<CopilotTokensPage />);

    expect(markup).toContain("—");
    expect(markup).not.toContain("$0.00");
    expect(markup).not.toContain('font-semibold">0<');
    expect(markup).toContain("— modelos");
    expect(markup).toContain("No se pudo cargar el uso de tokens.");
    expect(markup).toContain('role="alert"');
    expect(markup).toContain("No hay datos disponibles.");
  });

  it("shows real numbers when the summary payload is available", () => {
    queryState.current.data = {
      input_tokens: 100,
      output_tokens: 50,
      cache_creation_tokens: 10,
      cache_read_tokens: 5,
      calls: 7,
      cost_usd: 1.5,
      models: [],
    };

    const markup = renderToStaticMarkup(<CopilotTokensPage />);

    expect(markup).toContain("$1.50");
    expect(markup).toContain("165");
    expect(markup).toContain("0 modelos");
    expect(markup).not.toContain("— modelos");
  });

  it("marks the loading placeholder rows as busy status", () => {
    queryState.current.isLoading = true;

    const markup = renderToStaticMarkup(<CopilotTokensPage />);

    expect(markup).toContain('role="status"');
    expect(markup).toContain('aria-busy="true"');
  });
});
