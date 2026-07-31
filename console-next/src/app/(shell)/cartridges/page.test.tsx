import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CartridgesPage from "./page";

type FreshnessEntry = { age_hours: number | null; status: "fresh" | "stale" | "very_stale" | "never" };

const kpisMock = vi.hoisted(() =>
  vi.fn((): { data?: { data_freshness: Record<string, FreshnessEntry> } } => ({ data: undefined })),
);
const listMock = vi.hoisted(() =>
  vi.fn(() => ({
    isLoading: false,
    isError: false,
    data: { cartridges: ["replicon"] },
    refetch: vi.fn(),
  })),
);

vi.mock("@/lib/hooks/useKpis", () => ({
  useKpis: () => kpisMock(),
}));

vi.mock("@/lib/hooks/useCartridges", () => ({
  useCartridgeList: () => listMock(),
  useActivateCartridge: () => ({ isPending: false, variables: undefined, mutateAsync: vi.fn() }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

function renderWithFreshness(entry: FreshnessEntry | undefined): string {
  kpisMock.mockReturnValue({
    data: entry ? { data_freshness: { replicon: entry } } : { data_freshness: {} },
  });
  return renderToStaticMarkup(<CartridgesPage />);
}

beforeEach(() => {
  vi.clearAllMocks();
  listMock.mockReturnValue({
    isLoading: false,
    isError: false,
    data: { cartridges: ["replicon"] },
    refetch: vi.fn(),
  });
});

describe("CartridgesPage freshness-to-status mapping", () => {
  it("does NOT show a stale cartridge as Conectado", () => {
    const markup = renderWithFreshness({ status: "stale", age_hours: 30 });

    expect(markup).toContain("Datos antiguos");
    expect(markup).not.toContain("Conectado");
    expect(markup).not.toContain("Falló");
  });

  it("does NOT show a very_stale cartridge as Falló", () => {
    const markup = renderWithFreshness({ status: "very_stale", age_hours: 120 });

    expect(markup).toContain("Datos muy antiguos");
    expect(markup).not.toContain("Falló");
    expect(markup).not.toContain("Conectado");
  });

  it("still shows fresh data as Conectado", () => {
    const markup = renderWithFreshness({ status: "fresh", age_hours: 1 });

    expect(markup).toContain("Conectado");
    expect(markup).not.toContain("Datos antiguos");
  });

  it("shows never-extracted cartridges as Sin configurar", () => {
    const markup = renderWithFreshness({ status: "never", age_hours: null });

    expect(markup).toContain("Sin configurar");
    expect(markup).not.toContain("Conectado");
    expect(markup).not.toContain("Falló");
  });

  it("shows Sin configurar when the KPI payload has no entry", () => {
    const markup = renderWithFreshness(undefined);

    expect(markup).toContain("Sin configurar");
  });
});
