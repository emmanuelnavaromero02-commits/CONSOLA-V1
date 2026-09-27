// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Automation } from "@/lib/operations/automations-client";

import { AIRFLOW_UNAVAILABLE_NOTE, AUTOMATIONS_ERROR, AutomationsPanel } from "./AutomationsPanel";

const boundary = vi.hoisted(() => ({ listAutomations: vi.fn() }));

vi.mock("@/lib/operations/automations-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/operations/automations-client")>();
  return { ...actual, ...boundary };
});
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

const SCHEDULED: Automation = {
  dag_id: "sap_b1_refresh",
  label: "Refresco de SAP Business One",
  cartridge_id: "sap_b1",
  kind: "scheduled",
  schedule_description: "Cada 10 minutos",
  state: "paused_by_operator",
  state_note_es: "En pausa por un operador de plataforma; no se ejecutará en su horario",
  active_runs: 0,
  last_run: { status: "success", started_at: "2026-09-25T10:00:00Z", finished_at: "2026-09-25T10:05:00Z" },
  runs_known: true,
};

const MANUAL: Automation = {
  dag_id: "sap_sf_extract",
  label: "Extracción SuccessFactors",
  cartridge_id: "sap_successfactors",
  kind: "manual",
  schedule_description: null,
  state: "paused_manual",
  state_note_es: "Se activa automáticamente al pulsar Extraer",
  active_runs: 12,
  last_run: null,
  runs_known: true,
};

const PLATFORM: Automation = {
  dag_id: "entity_scheduler",
  label: "Meta-scheduler",
  cartridge_id: null,
  kind: "scheduled",
  schedule_description: "Cada 5 minutos",
  state: "active",
  state_note_es: "Activa: se ejecuta en su horario.",
  active_runs: null,
  last_run: null,
  runs_known: false,
};

async function render() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <AutomationsPanel />
      </QueryClientProvider>,
    );
  });
  for (let index = 0; index < 4; index += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

beforeEach(() => {
  vi.clearAllMocks();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("AutomationsPanel", () => {
  it("shows real state notes, schedules and a link to runs without any toggle", async () => {
    boundary.listAutomations.mockResolvedValue({
      schema_version: "pipeline-automations/v1",
      checked_at: "2026-09-26T10:00:00Z",
      airflow_available: true,
      automations: [SCHEDULED, MANUAL, PLATFORM],
    });
    await render();
    const scheduled = container.querySelector('[data-automation="sap_b1_refresh"]');
    expect(scheduled?.textContent).toContain("Programada");
    expect(scheduled?.textContent).toContain("Cada 10 minutos");
    expect(scheduled?.textContent).toContain("En pausa por un operador de plataforma");
    expect(scheduled?.textContent).toContain("Exitosa");
    const manual = container.querySelector('[data-automation="sap_sf_extract"]');
    expect(manual?.textContent).toContain("Manual");
    expect(manual?.textContent).toContain("Se activa automáticamente al pulsar Extraer");
    expect(manual?.textContent).toContain("12");
    expect(manual?.textContent).toContain("Sin ejecuciones registradas");
    const platform = container.querySelector('[data-automation="entity_scheduler"]');
    expect(platform?.textContent).toContain("Plataforma");
    expect(platform?.textContent).toContain("Sin información");
    expect(platform?.textContent).not.toContain("Sin ejecuciones");
    expect(container.querySelector('a[href="/monitor"]')?.textContent).toBe("Ver ejecuciones");
    const labels = [...container.querySelectorAll("button")].map((node) => node.textContent?.trim());
    expect(labels.some((label) => /pausar|reanudar|activar|ejecutar/i.test(label ?? ""))).toBe(false);
    expect(container.querySelector('input[type="checkbox"], [role="switch"]')).toBeNull();
    expect(container.textContent).toContain("Fuente de datos");
    expect(container.textContent).not.toMatch(/cartucho/i);
  });

  it("is honest when Airflow cannot be read", async () => {
    boundary.listAutomations.mockResolvedValue({
      schema_version: "pipeline-automations/v1",
      checked_at: "2026-09-26T10:00:00Z",
      airflow_available: false,
      automations: [
        {
          ...MANUAL,
          state: "unavailable",
          state_note_es: "No se pudo consultar Airflow en este momento; estado desconocido.",
          active_runs: null,
          runs_known: false,
        },
      ],
    });
    await render();
    expect(container.textContent).toContain(AIRFLOW_UNAVAILABLE_NOTE);
    expect(container.textContent).toContain("Sin información");
  });

  it("uses the shared empty state and announces load errors", async () => {
    boundary.listAutomations.mockResolvedValue({
      schema_version: "pipeline-automations/v1",
      checked_at: "2026-09-26T10:00:00Z",
      airflow_available: true,
      automations: [],
    });
    await render();
    expect(container.querySelector('[data-testid="empty-state"]')?.textContent).toContain(
      "Sin automatizaciones visibles",
    );

    await act(async () => root.unmount());
    root = createRoot(container);
    boundary.listAutomations.mockRejectedValue(new Error("boom"));
    await render();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(AUTOMATIONS_ERROR);
  });
});
