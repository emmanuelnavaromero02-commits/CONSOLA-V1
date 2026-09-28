// @vitest-environment jsdom

import { act } from "react";
import type { ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  search: "",
  getMeAccess: vi.fn(),
  listCustomerCartridges: vi.fn(),
  listMarketplaceProducts: vi.fn(),
  cartridgeList: vi.fn(),
  kpis: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(state.search),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@/lib/admin-surfaces", () => ({
  getMeAccess: (...args: unknown[]) => state.getMeAccess(...args),
}));

vi.mock("@/lib/marketplace", () => ({
  listCustomerCartridges: (...args: unknown[]) => state.listCustomerCartridges(...args),
  listMarketplaceProducts: (...args: unknown[]) => state.listMarketplaceProducts(...args),
  requestMarketplaceProduct: vi.fn(),
  retryMarketplaceInstallation: vi.fn(),
  listAdminInstallations: vi.fn(async () => ({ installations: [] })),
  runAdminInstallationAction: vi.fn(),
  getInstallationAccess: vi.fn(async () => ({ users: [] })),
  setInstallationUserAccess: vi.fn(),
}));

vi.mock("@/lib/hooks/useCartridges", () => ({
  useCartridgeList: () => state.cartridgeList(),
  useActivateCartridge: () => ({ isPending: false, variables: undefined, mutateAsync: vi.fn() }),
}));

vi.mock("@/lib/hooks/useKpis", () => ({
  useKpis: () => state.kpis(),
}));

import { MarketplaceConsole } from "./MarketplaceConsole";

let root: Root | null = null;
let container: HTMLDivElement;
let client: QueryClient;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  state.search = "";
  state.getMeAccess.mockReset().mockResolvedValue({
    ui_capabilities: {
      can_view_marketplace: true,
      can_view_cartridges: true,
      can_admin_marketplace: false,
    },
  });
  state.listCustomerCartridges.mockReset().mockResolvedValue({ installations: [] });
  state.listMarketplaceProducts.mockReset().mockResolvedValue({ products: [] });
  state.cartridgeList.mockReset().mockReturnValue({
    data: { cartridges: ["replicon"] },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  });
  state.kpis.mockReset().mockReturnValue({ data: undefined, isLoading: false, isError: true, refetch: vi.fn() });
});

afterEach(async () => {
  if (root) {
    await act(async () => {
      root?.unmount();
    });
    root = null;
  }
  client.clear();
  container.remove();
});

async function render(): Promise<HTMLDivElement> {
  root = createRoot(container);
  await act(async () => {
    root?.render(
      <QueryClientProvider client={client}>
        <MarketplaceConsole />
      </QueryClientProvider>,
    );
  });
  for (let i = 0; i < 10; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  return container;
}

describe("MarketplaceShell access handling", () => {
  it("renders a retryable error instead of failing open when /api/me/access errors", async () => {
    state.getMeAccess.mockRejectedValue(new Error("boom"));
    await render();
    expect(container.textContent).toContain("No se pudo cargar tu acceso.");
    expect(container.textContent).toContain("Reintentar");
    expect(container.textContent).not.toContain("Catálogo de fuentes de datos");
    expect(container.textContent).not.toContain("Licencias y solicitudes");
  });

  it("treats a valid but disallowed ?tab=licencias like no request", async () => {
    state.search = "tab=licencias";
    await render();
    expect(container.textContent).toContain("Catálogo de fuentes de datos");
    expect(container.textContent).not.toContain("Licencias y solicitudes");
  });
});

describe("Conectadas view without freshness data", () => {
  it("keeps skeletons while the KPI freshness query loads", async () => {
    state.search = "tab=conectadas";
    state.kpis.mockReturnValue({ data: undefined, isLoading: true, isError: false, refetch: vi.fn() });
    await render();
    const section = container.querySelector('[aria-label="Fuentes de datos conectadas"]');
    expect(section?.querySelector(".animate-pulse")).toBeTruthy();
    expect(container.textContent).not.toContain("Sin configurar");
  });

  it("shows a neutral badge, never Sin configurar, when the KPI query errored", async () => {
    state.search = "tab=conectadas";
    await render();
    const section = container.querySelector('[aria-label="Fuentes de datos conectadas"]');
    expect(section?.textContent).toContain("Replicon");
    expect(section?.textContent).toContain("Sin información");
    expect(container.textContent).not.toContain("Sin configurar");
  });
});

describe("Conectadas counters without real data", () => {
  it("shows Sin información instead of zero when the installations query is disabled", async () => {
    state.getMeAccess.mockResolvedValue({
      ui_capabilities: {
        can_view_marketplace: false,
        can_view_cartridges: true,
        can_admin_marketplace: false,
      },
    });
    await render();
    const tiles = Array.from(
      container.querySelectorAll('[aria-label="Métricas de fuentes conectadas"] article'),
    );
    const instaladas = tiles.find((tile) => tile.textContent?.includes("Instaladas"));
    expect(instaladas?.textContent).toContain("Sin información");
    const fuentes = tiles.find((tile) => tile.textContent?.includes("Fuentes"));
    expect(fuentes?.textContent).toContain("1");
  });

  it("shows real numbers once the installations query resolves", async () => {
    state.search = "tab=conectadas";
    state.listCustomerCartridges.mockResolvedValue({
      installations: [{ id: "i1", cartridge_id: "replicon", status: "ready" }],
    });
    await render();
    const tiles = Array.from(
      container.querySelectorAll('[aria-label="Métricas de fuentes conectadas"] article'),
    );
    const instaladas = tiles.find((tile) => tile.textContent?.includes("Instaladas"));
    expect(instaladas?.textContent).toContain("1");
    expect(instaladas?.textContent).not.toContain("Sin información");
  });
});
