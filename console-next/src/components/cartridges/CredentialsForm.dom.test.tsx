// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SyncRunPayload } from "@/lib/sync-now";

import { SyncRunStatusCard } from "./CredentialsForm";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock("@/lib/hooks/useCartridges", () => ({
  useTestConnection: () => ({ isPending: false, mutateAsync: vi.fn() }),
}));
vi.mock("@/lib/operations/hooks", () => ({
  useVaultConnections: () => ({ data: { connections: [] } }),
}));
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

function payload(overrides: Partial<SyncRunPayload> = {}): SyncRunPayload {
  return {
    run_id: "sync_now:sap_successfactors:abc",
    cartridge_id: "sap_successfactors",
    status: "running",
    mode: "incremental",
    target: "all",
    steps: [
      { id: "connection", label: "Conexión", status: "running" },
      { id: "bronze", label: "Bronze", status: "queued" },
    ],
    triggered_entities: [],
    errors: [],
    control_room_ready: false,
    ...overrides,
  };
}

describe("SyncRunStatusCard", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  async function render(data: SyncRunPayload, loading = false) {
    await act(async () => {
      root.render(<SyncRunStatusCard cartridgeId="sap_successfactors" payload={data} loading={loading} />);
    });
  }

  it("titles the card from the real status and translates it", async () => {
    await render(payload({ status: "failed", error_message: "algo falló" }));

    expect(container.textContent).toContain("La sincronización falló");
    expect(container.textContent).toContain("Fallida");
    expect(container.textContent).toContain("algo falló");
    expect(container.textContent).not.toContain("Sincronización completa");
  });

  it("shows partial runs as completed with warnings", async () => {
    await render(payload({ status: "partial" }));

    expect(container.textContent).toContain("Completada con advertencias");
  });

  it("shows honest copy for blocked runs and stops treating them as live", async () => {
    await render(payload({ status: "blocked" }));

    expect(container.textContent).toContain("Sincronización bloqueada");
    expect(container.textContent).toContain("Bloqueada");
    await act(async () => {
      vi.advanceTimersByTime(11_000);
    });
    expect(container.textContent).not.toContain("Monitoreando avance");
  });

  it("translates known error reason codes into business Spanish", async () => {
    await render(
      payload({
        status: "failed",
        errors: [
          { entity: "__connection__", reason: "connection_probe_failed" },
          { entity: "__extract_all__", reason: "airflow_trigger_failed" },
          { entity: "EmpJob", reason: "mystery_reason" },
        ],
      }),
    );

    expect(container.textContent).toContain("No se pudo validar la conexión con el origen.");
    expect(container.textContent).toContain("No se pudo iniciar la extracción en el orquestador.");
    expect(container.textContent).not.toContain("mystery_reason");
  });

  it("translates step statuses instead of leaking raw values", async () => {
    await render(
      payload({
        status: "failed",
        steps: [{ id: "connection", label: "Conexión", status: "failed", detail: "detalle" }],
      }),
    );

    expect(container.textContent).toContain("Falló");
    const stepStatuses = [...container.querySelectorAll("p")].map((node) => node.textContent);
    expect(stepStatuses).not.toContain("failed");
  });

  it("shows the auto-unpause automation notice when present", async () => {
    await render(payload({ automation: { was_paused: true, unpaused: true, message_es: null } }));

    expect(container.textContent).toContain("La automatización estaba en pausa y se reactivó automáticamente.");
  });

  it("shows liveness feedback after 10 seconds without completed steps", async () => {
    await render(payload());

    expect(container.textContent).not.toContain("Monitoreando avance");
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });

    expect(container.textContent).toContain("Extracción iniciada en el orquestador. Monitoreando avance…");
    const link = [...container.querySelectorAll("a")].find((a) => a.textContent?.includes("Ver estado del pipeline"));
    expect(link?.getAttribute("href")).toBe("/monitor");
  });

  it("reports an unhealthy orchestrator in the liveness message", async () => {
    await render(
      payload({
        orchestrator: { airflow_available: true, scheduler_healthy: false, dag_paused: false },
      }),
    );
    await act(async () => {
      vi.advanceTimersByTime(10_000);
    });

    expect(container.textContent).toContain("Verificando estado del orquestador…");
  });

  it("keeps liveness hidden once a step completed or the run is terminal", async () => {
    await render(
      payload({
        steps: [{ id: "connection", label: "Conexión", status: "success" }],
      }),
    );
    await act(async () => {
      vi.advanceTimersByTime(11_000);
    });
    expect(container.textContent).not.toContain("Monitoreando avance");

    await render(payload({ status: "success" }));
    await act(async () => {
      vi.advanceTimersByTime(11_000);
    });
    expect(container.textContent).not.toContain("Monitoreando avance");
    expect(container.textContent).toContain("Sincronización completa");
  });
});
