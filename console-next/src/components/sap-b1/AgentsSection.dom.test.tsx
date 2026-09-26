// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ControlAlert, ControlItem } from "@/lib/control-room/types";

import { AgentsSection } from "./AgentsSection";

const boundary = vi.hoisted(() => ({
  listAlerts: vi.fn(),
  getControlRoomDashboard: vi.fn(),
  createItemDecision: vi.fn(),
  markAlertFalsePositive: vi.fn(),
  getSapB1View: vi.fn(),
}));

vi.mock("@/lib/control-room/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/control-room/client")>()),
  listAlerts: boundary.listAlerts,
  getControlRoomDashboard: boundary.getControlRoomDashboard,
  createItemDecision: boundary.createItemDecision,
  markAlertFalsePositive: boundary.markAlertFalsePositive,
}));

vi.mock("@/lib/sap-b1/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/sap-b1/client")>()),
  getSapB1View: boundary.getSapB1View,
}));

vi.mock("@/lib/control-room/use-wisdom-bit-monitors", () => {
  const idle = { data: undefined, isPending: false, isError: false, isFetching: false, error: null, refetch: () => undefined };
  return { useWisdomBitMonitors: () => ({ agents: idle, ops: idle, monitors: [], runsByAgent: {} }) };
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;

function alert(itemId: string, overrides: Partial<ControlAlert> = {}): ControlAlert {
  return { id: `alert:${itemId}`, item_id: itemId, kind: "", cartridge: "sap_b1", severity: "high", status: "open", ...overrides };
}

function item(id: string, overrides: Partial<ControlItem> = {}): ControlItem {
  return { id, kind: "agent_alert", cartridge: "sap_b1", status: "open", omega: { decision: { status: "open", label: "Pendiente" } }, ...overrides };
}

const OLD = "agent_alert:0001";
const NEW = "agent_alert:0002";

function alertsPayload(alerts: ControlAlert[]) {
  return { alerts, summary: {}, generated_at: "2026-09-25T13:00:00Z" };
}

async function flush() {
  for (let index = 0; index < 4; index += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function render(canWrite = true) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <AgentsSection canReadAgents={false} canWrite={canWrite} />
      </QueryClientProvider>,
    );
  });
  await flush();
}

function row(itemId: string) {
  return container.querySelector<HTMLTableRowElement>(`tr[data-item-id="${itemId}"]`);
}

function rowButton(itemId: string, label: string) {
  return [...(row(itemId)?.querySelectorAll("button") ?? [])].find((node) => node.textContent?.trim() === label);
}

async function click(node: Element | null | undefined) {
  expect(node).toBeTruthy();
  await act(async () => (node as HTMLElement).click());
  await flush();
}

beforeEach(() => {
  vi.clearAllMocks();
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  boundary.getSapB1View.mockResolvedValue({ status: "ready", metrics: {} });
  boundary.listAlerts.mockResolvedValue(
    alertsPayload([
      alert(OLD, { title: "Margen bajo en clínica norte" }),
      alert("intel:1", { cartridge: "sap_successfactors", title: "Rotación alta" }),
      alert(NEW, { title: "Lote por caducar", severity: "critical" }),
    ]),
  );
  boundary.getControlRoomDashboard.mockResolvedValue({
    items: [
      item(OLD, { detected_at: "2026-09-20T12:00:00Z" }),
      item(NEW, { detected_at: "2026-09-24T12:00:00Z", status: "decision_created", omega: { decision: { status: "decision_created", label: "Decision #7" } } }),
    ],
  });
  boundary.createItemDecision.mockResolvedValue({ item: { status: "decision_created" } });
  boundary.markAlertFalsePositive.mockResolvedValue({ ok: true, alert: null, item: { status: "dismissed" } });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  queryClient.clear();
  vi.restoreAllMocks();
});

describe("AgentsSection alerts", () => {
  it("lists only SAP Business One alerts, newest first, with the state from the item", async () => {
    await render();
    const rows = [...container.querySelectorAll("tr[data-item-id]")].map((node) => node.getAttribute("data-item-id"));
    expect(rows).toEqual([NEW, OLD]);
    expect(container.textContent).not.toContain("Rotación alta");

    expect(row(NEW)?.textContent).toContain("Lote por caducar");
    expect(row(NEW)?.textContent).toContain("Crítica");
    expect(row(NEW)?.textContent).toContain("Decisión registrada");
    expect(row(NEW)?.querySelector('a[href="/decisions"]')?.textContent).toBe("Ver en Decisiones");
    expect(rowButton(NEW, "Registrar decisión")?.disabled).toBe(true);
    expect(rowButton(NEW, "Marcar falso positivo")?.disabled).toBe(false);

    expect(row(OLD)?.textContent).toContain("Alta");
    expect(row(OLD)?.textContent).toContain("Abierta");
    expect(row(OLD)?.querySelector('a[href="/decisions"]')).toBeNull();
    expect(rowButton(OLD, "Registrar decisión")?.disabled).toBe(false);
  });

  it("records a decision, links to Decisiones and refetches the alerts", async () => {
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    await render();
    expect(boundary.listAlerts).toHaveBeenCalledTimes(1);

    await click(rowButton(OLD, "Registrar decisión"));

    expect(boundary.createItemDecision).toHaveBeenCalledTimes(1);
    expect(boundary.createItemDecision.mock.calls[0][0]).toBe(OLD);
    expect(row(OLD)?.textContent).toContain("Decisión registrada");
    expect(row(OLD)?.querySelector('a[href="/decisions"]')).not.toBeNull();
    expect(rowButton(OLD, "Registrar decisión")?.disabled).toBe(true);
    expect(boundary.listAlerts).toHaveBeenCalledTimes(2);
    expect(boundary.getControlRoomDashboard).toHaveBeenCalledTimes(2);
    const keys = invalidate.mock.calls.map(([filters]) => JSON.stringify(filters?.queryKey));
    expect(keys).toEqual(
      expect.arrayContaining([
        JSON.stringify(["control-room", "alerts"]),
        JSON.stringify(["control-room", "dashboard"]),
        JSON.stringify(["sap-b1", "view", "sap_b1_learning_kpis"]),
        JSON.stringify(["decisions"]),
      ]),
    );
  });

  it("marks a false positive with an optional reason and drops it after the refetch", async () => {
    const prompt = vi.spyOn(window, "prompt").mockReturnValueOnce(null).mockReturnValueOnce("  pedido ya surtido  ");
    await render();

    await click(rowButton(OLD, "Marcar falso positivo"));
    expect(boundary.markAlertFalsePositive).not.toHaveBeenCalled();

    boundary.listAlerts.mockResolvedValue(alertsPayload([alert(NEW, { title: "Lote por caducar" })]));
    await click(rowButton(OLD, "Marcar falso positivo"));

    expect(prompt).toHaveBeenCalledTimes(2);
    expect(boundary.markAlertFalsePositive).toHaveBeenCalledWith(OLD, "pedido ya surtido");
    expect(row(OLD)).toBeNull();
    expect(container.textContent).toContain("Falso positivo registrado");
    expect(container.textContent).toContain("«Margen bajo en clínica norte» salió de las alertas activas.");
  });

  it("explains a conflict when the alert already changed", async () => {
    boundary.createItemDecision.mockRejectedValue(Object.assign(new Error("terminal control room item"), { status: 409 }));
    await render();
    await click(rowButton(OLD, "Registrar decisión"));
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "No se pudo registrar la decisión: La alerta ya cambió de estado; actualiza la lista.",
    );
    expect(row(OLD)?.textContent).toContain("Abierta");
  });

  it("shows an empty state when the agents have not raised alerts", async () => {
    boundary.listAlerts.mockResolvedValue(alertsPayload([alert("intel:1", { cartridge: "sap_successfactors" })]));
    await render();
    expect(container.textContent).toContain("Sin alertas de los agentes");
    expect(container.querySelector("tr[data-item-id]")).toBeNull();
  });

  it("keeps the list read-only without control_room.write", async () => {
    await render(false);
    expect(row(OLD)).not.toBeNull();
    expect(container.querySelectorAll("tr[data-item-id] button")).toHaveLength(0);
    expect(container.textContent).not.toContain("Acciones");
    expect(container.textContent).toContain("requiere permiso de escritura en Control Room");
  });
});
