import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { KpiPayload } from "@/lib/hooks/useKpis";

import DashboardPage from "./page";

interface MockKpisState {
  data: KpiPayload | undefined;
  isLoading: boolean;
  isError: boolean;
  refetch: () => Promise<unknown>;
}

const kpisState = vi.hoisted(() => ({
  current: {
    data: undefined,
    isLoading: true,
    isError: false,
    refetch: async () => undefined,
  } as MockKpisState,
}));

vi.mock("@/lib/hooks/useKpis", () => ({
  useKpis: () => kpisState.current,
}));

vi.mock("@/components/dashboard/BriefingSection", () => ({
  BriefingSection: () => null,
}));

vi.mock("@/components/auth/LogoutButton", () => ({
  LogoutButton: () => null,
}));

vi.mock("next/link", () => ({
  default: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}));

function fullPayload(overrides: Partial<KpiPayload> = {}): KpiPayload {
  return {
    cartridges: { total: 5, connected: 5, disconnected: 0 },
    extractions: { today: 3, week: 12 },
    data_freshness: {},
    users: { active_today: 2, total: 9 },
    copilot: { conversations_today: 1, tools_invoked_today: 4 },
    audit: { events_today: 7, destructive_actions_today: 0 },
    ...overrides,
  };
}

beforeEach(() => {
  kpisState.current = {
    data: undefined,
    isLoading: true,
    isError: false,
    refetch: async () => undefined,
  };
});

describe("DashboardPage: ausencia/error no afirma éxito", () => {
  it("durante la carga no afirma 'Todos en línea' ni 'Sin movimientos'", () => {
    const markup = renderToStaticMarkup(<DashboardPage />);

    expect(markup).not.toContain("Todos en línea");
    expect(markup).not.toContain("Sin movimientos");
  });

  it("ante error sin datos muestra indisponible, nunca éxito", () => {
    kpisState.current = {
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: async () => undefined,
    };

    const markup = renderToStaticMarkup(<DashboardPage />);

    expect(markup).toContain("No se pudieron cargar los indicadores.");
    expect(markup).not.toContain("Todos en línea");
    expect(markup).not.toContain("Sin movimientos");
    expect(markup).toContain("No disponible");
  });

  it("con payload sin el campo que lo demuestra muestra 'Sin datos', no éxito", () => {
    kpisState.current = {
      data: fullPayload({
        cartridges: { total: 5, connected: 5 } as KpiPayload["cartridges"],
        audit: { events_today: 7 } as KpiPayload["audit"],
      }),
      isLoading: false,
      isError: false,
      refetch: async () => undefined,
    };

    const markup = renderToStaticMarkup(<DashboardPage />);

    expect(markup).not.toContain("Todos en línea");
    expect(markup).not.toContain("Sin movimientos");
    expect(markup).toContain("Sin datos");
  });

  it("solo con payload válido que lo demuestra afirma éxito", () => {
    kpisState.current = {
      data: fullPayload(),
      isLoading: false,
      isError: false,
      refetch: async () => undefined,
    };

    const markup = renderToStaticMarkup(<DashboardPage />);

    expect(markup).toContain("Todos en línea");
    expect(markup).toContain("Sin movimientos");
  });

  it("con desconectados y acciones destructivas reales no afirma éxito", () => {
    kpisState.current = {
      data: fullPayload({
        cartridges: { total: 5, connected: 3, disconnected: 2 },
        audit: { events_today: 7, destructive_actions_today: 1 },
      }),
      isLoading: false,
      isError: false,
      refetch: async () => undefined,
    };

    const markup = renderToStaticMarkup(<DashboardPage />);

    expect(markup).toContain("2 sin conexión");
    expect(markup).toContain("Revisar audit log");
    expect(markup).not.toContain("Todos en línea");
    expect(markup).not.toContain("Sin movimientos");
  });
});
