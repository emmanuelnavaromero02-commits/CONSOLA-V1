// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentRecord } from "@/lib/admin-surfaces";
import { forbiddenTermsIn } from "@/lib/glossary";

import { GuardiansCatalog } from "./GuardiansCatalog";

const api = vi.hoisted(() => ({
  listAgents: vi.fn(),
  listAgentRuns: vi.fn(),
  invokeAgent: vi.fn(),
  getMeAccess: vi.fn(),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock("@/lib/admin-surfaces", () => api);

const GUARDIAN: AgentRecord = {
  id: "g-1",
  name: "Controller Financiero Monitor",
  slug: "sap_s4hana_controller_financiero_monitor",
  cartridge_id: "sap_s4hana",
  description: "Monitor programado de Finanzas: revisa margen por proyecto y publica evidencia en Control Room.",
  extra: {
    role: "monitor",
    monitor: { wisdom_bit_id: "WB-FINANZAS" },
    schedule: { cron: "2,17,32,47 * * * *", tz: "UTC", enabled: true, prompt: "Revisa Finanzas ahora." },
  },
  is_active: true,
};

const GENERAL: AgentRecord = {
  id: "a-9",
  name: "Asistente general",
  slug: "asistente_general",
  cartridge_id: "replicon",
  extra: { role: "", category: "cartridge" },
  is_active: true,
};

let container: HTMLDivElement;
let root: Root;

async function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <GuardiansCatalog onOpenAdmin={() => undefined} />
      </QueryClientProvider>,
    );
  });
  await settle();
}

async function settle() {
  for (let index = 0; index < 5; index += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

function button(text: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll<HTMLButtonElement>("button")].find(
    (element) => element.textContent?.trim() === text,
  );
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  api.listAgents.mockResolvedValue([GUARDIAN, GENERAL]);
  api.listAgentRuns.mockResolvedValue([
    { id: 7, status: "success", started_at: "2026-09-27T13:02:00Z", finished_at: "2026-09-27T13:03:00Z" },
    { id: 6, status: "failed", started_at: "2026-09-26T13:02:00Z" },
  ]);
  api.getMeAccess.mockResolvedValue({ permissions: ["agents.read", "agents.execute"] });
  api.invokeAgent.mockResolvedValue({ queued: true, run_id: 8 });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("GuardiansCatalog", () => {
  it("shows only monitor agents with real objective, frequency, last run and health", async () => {
    await render();
    const cards = container.querySelectorAll('[data-testid="guardian-card"]');
    expect(cards).toHaveLength(1);
    expect(container.textContent).toContain("Controller Financiero Monitor");
    expect(container.textContent).toContain("SAP S/4HANA");
    expect(container.textContent).toContain("revisa margen por proyecto");
    expect(container.textContent).toContain("Varias veces por hora (:02, :17, :32, :47)");
    expect(container.textContent).toContain("Completada");
    expect(container.textContent).toContain("Última corrida exitosa");
    expect(container.textContent).not.toContain("Asistente general");
    expect(container.textContent).not.toContain("sap_s4hana_controller_financiero_monitor");
    expect(container.textContent).not.toContain("success");
    expect(forbiddenTermsIn(container.textContent ?? "")).toEqual([]);
  });

  it("fires the inspection through the invoke client in background", async () => {
    await render();
    const run = button("Inspeccionar ahora");
    expect(run).toBeTruthy();
    await act(async () => {
      run?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await settle();
    expect(api.invokeAgent).toHaveBeenCalledWith("g-1", "Revisa Finanzas ahora.", [], { background: true });
  });

  it("hides the inspection button without the execute permission", async () => {
    api.getMeAccess.mockResolvedValue({ permissions: ["agents.read"] });
    await render();
    expect(button("Inspeccionar ahora")).toBeUndefined();
  });

  it("hides the inspection button for inactive guardians and says so", async () => {
    api.listAgents.mockResolvedValue([{ ...GUARDIAN, is_active: false }]);
    await render();
    expect(button("Inspeccionar ahora")).toBeUndefined();
    expect(container.textContent).toContain("Inactivo");
  });

  it("is honest when the workspace has no guardians", async () => {
    api.listAgents.mockResolvedValue([GENERAL]);
    await render();
    expect(container.textContent).toContain("No hay guardianes registrados en este espacio de trabajo");
    expect(api.invokeAgent).not.toHaveBeenCalled();
  });

  it("is honest when there are no runs yet", async () => {
    api.listAgentRuns.mockResolvedValue([]);
    await render();
    expect(container.textContent).toContain("Sin corridas registradas");
  });
});
