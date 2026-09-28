// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { StudioDagsHealthPayload, StudioDagsPayload } from "@/lib/studio/types";

import { DagsPanel, extractionDagTarget } from "./DagsPanel";

type Mutation = { mutate: ReturnType<typeof vi.fn>; isPending: boolean };

function query<T>(data: T, extra: Record<string, unknown> = {}) {
  return {
    data,
    isLoading: false,
    isError: false,
    isSuccess: true,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
    ...extra,
  };
}

function mutation(extra: Partial<Mutation> = {}): Mutation {
  return { mutate: vi.fn(), isPending: false, ...extra };
}

const state = vi.hoisted(() => ({ hooks: {} as Record<string, unknown> }));
const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }));

vi.mock("sonner", () => ({ toast: toastMock }));

vi.mock("@/lib/studio/hooks", () => {
  const pick = (name: string) => () => state.hooks[name];
  return {
    useStudioDags: pick("useStudioDags"),
    useStudioDagsHealth: pick("useStudioDagsHealth"),
    useRetryExtractAll: pick("useRetryExtractAll"),
    useDagSource: pick("useDagSource"),
    useDagTemplates: pick("useDagTemplates"),
    useSystemInfo: pick("useSystemInfo"),
    useRuntimeConfig: pick("useRuntimeConfig"),
    useDeployDag: pick("useDeployDag"),
    useRenameDag: pick("useRenameDag"),
    useDeleteDag: pick("useDeleteDag"),
  };
});

function dagsPayload(): StudioDagsPayload {
  return {
    cartridge: "acme",
    total: 4,
    dags: [
      { dag_id: "acme_cleanup", is_paused: true, is_active: true },
      { dag_id: "acme_extract", is_paused: false, is_active: true },
      { dag_id: "acme_extract_all", is_paused: false, is_active: true },
      { dag_id: "acme_refresh_gold", is_paused: false, is_active: true },
    ],
  };
}

function healthPayload(): StudioDagsHealthPayload {
  return {
    cartridge: "acme",
    airflow_available: true,
    scheduler_healthy: true,
    total: 4,
    dags: [
      {
        dag_id: "acme_cleanup",
        last_run: {
          state: "success",
          start_date: "2026-09-27T10:00:00+00:00",
          end_date: "2026-09-27T10:05:30+00:00",
          duration_seconds: 330,
        },
      },
      {
        dag_id: "acme_extract",
        last_run: { state: "failed", start_date: "2026-09-27T09:00:00+00:00" },
        failed_task_id: "extract_entities",
        error_es: "Credenciales rechazadas por el sistema de origen",
      },
      {
        dag_id: "acme_extract_all",
        last_run: { state: "failed", start_date: "2026-09-27T08:00:00+00:00" },
        failed_task_id: "trigger_children",
        error_es: "No se pudo alcanzar el sistema de origen",
      },
      { dag_id: "acme_refresh_gold", last_run: { state: "running" } },
    ],
  };
}

describe("DagsPanel semaphore", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    state.hooks = {
      useStudioDags: query(dagsPayload()),
      useStudioDagsHealth: query(healthPayload()),
      useRetryExtractAll: mutation(),
      useDagSource: query({ dag_id: "acme_extract_all", found: true, source_code: "code", path: null }),
      useDagTemplates: query([]),
      useSystemInfo: query({ dev_mode: true, rce_tools_enabled: true, dag_deploy_enabled: true }),
      useRuntimeConfig: query({ airflow_url: null }),
      useDeployDag: mutation(),
      useRenameDag: mutation(),
      useDeleteDag: mutation(),
    };
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  async function render() {
    await act(async () => {
      root.render(<DagsPanel cartridge="acme" />);
    });
  }

  function rowFor(dagId: string): HTMLElement {
    const button = container.querySelector(`[data-dag-id="${dagId}"]`);
    expect(button).toBeTruthy();
    return button!.closest("li") as HTMLElement;
  }

  it("shows green, amber and red states from real last runs", async () => {
    await render();

    const green = rowFor("acme_cleanup");
    expect(green.textContent).toContain("Operando");
    expect(green.textContent).toContain("5 min 30 s");
    expect(rowFor("acme_refresh_gold").textContent).toContain("En curso");
    expect(rowFor("acme_extract_all").textContent).toContain("Con fallas");
  });

  it("keeps the paused badge next to the semaphore", async () => {
    await render();

    expect(rowFor("acme_cleanup").textContent).toContain("Pausado");
  });

  it("offers retry only for the cartridge's extract_all DAG", async () => {
    const retry = mutation();
    state.hooks.useRetryExtractAll = retry;
    await render();

    const failedAll = rowFor("acme_extract_all");
    expect(failedAll.textContent).toContain("Tarea que falló:");
    expect(failedAll.textContent).toContain("trigger_children");
    expect(failedAll.textContent).toContain("No se pudo alcanzar el sistema de origen");
    const button = [...failedAll.querySelectorAll("button")].find((node) =>
      node.textContent?.includes("Reintentar extracción"),
    );
    expect(button).toBeTruthy();
    await act(async () => {
      button!.click();
    });
    expect(retry.mutate.mock.calls[0][0]).toBe("acme");
  });

  it("links entity extraction failures to the entities panel", async () => {
    await render();

    const failedEntity = rowFor("acme_extract");
    expect(failedEntity.textContent).toContain("extract_entities");
    expect(
      [...failedEntity.querySelectorAll("button")].some((node) =>
        node.textContent?.includes("Reintentar extracción"),
      ),
    ).toBe(false);
    const link = [...failedEntity.querySelectorAll("a")].find((node) =>
      node.textContent?.includes("Reintentar por entidad"),
    );
    expect(link?.getAttribute("href")).toBe("/studio?tab=entidades");
  });

  it("links non-extraction failures to workflows", async () => {
    const health = healthPayload();
    health.dags[3] = {
      dag_id: "acme_refresh_gold",
      last_run: { state: "failed" },
      failed_task_id: null,
      error_es: null,
    };
    state.hooks.useStudioDagsHealth = query(health);
    await render();

    const failed = rowFor("acme_refresh_gold");
    expect(failed.textContent).toContain("Sin información");
    expect(failed.textContent).toContain("La tarea falló; revisa el detalle técnico");
    const link = [...failed.querySelectorAll("a")].find((node) =>
      node.textContent?.includes("Ver automatizaciones"),
    );
    expect(link?.getAttribute("href")).toBe("/operations/workflows");
  });

  it("renders the semaphore even without a public Airflow URL and folds the editor", async () => {
    await render();
    await act(async () => {
      (container.querySelector('[data-dag-id="acme_extract_all"]') as HTMLElement).click();
    });

    expect(container.querySelector('[data-testid="dag-technical-details"]')).toBeTruthy();
    expect(container.textContent).toContain("Detalle técnico");
    expect(container.textContent).toContain("Enlace a Airflow no configurado.");
    expect(container.textContent).not.toContain("Airflow sin URL pública configurada.");
    expect(container.querySelectorAll('[data-testid="dag-semaphore"]').length).toBe(4);
  });

  it("warns when the scheduler is unhealthy and degrades quietly when health is unavailable", async () => {
    const unhealthy = healthPayload();
    unhealthy.scheduler_healthy = false;
    state.hooks.useStudioDagsHealth = query(unhealthy);
    await render();
    expect(container.querySelector('[data-testid="dags-scheduler-warning"]')).toBeTruthy();

    state.hooks.useStudioDagsHealth = query({
      cartridge: "acme",
      airflow_available: false,
      scheduler_healthy: null,
      dags: [],
      total: 0,
    });
    await render();
    expect(container.querySelector('[data-testid="dags-health-unavailable"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="dag-list"]')).toBeTruthy();
  });
});

describe("extractionDagTarget", () => {
  it("classifies extraction DAG ids", () => {
    expect(extractionDagTarget("acme_extract_all")).toEqual({ kind: "extract_all", cartridge: "acme" });
    expect(extractionDagTarget("sap_b1_extract")).toEqual({ kind: "extract", cartridge: "sap_b1" });
    expect(extractionDagTarget("acme_refresh_gold")).toBeNull();
    expect(extractionDagTarget("Acme_extract")).toBeNull();
  });
});
