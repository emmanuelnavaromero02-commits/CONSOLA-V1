// @vitest-environment jsdom

import type { ReactNode } from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DagGraph } from "./DagGraph";

type Mutation = { mutate: ReturnType<typeof vi.fn>; isPending: boolean };

function query<T>(data: T, extra: Record<string, unknown> = {}) {
  return { data, isLoading: false, isError: false, isSuccess: true, isFetching: false, error: null, refetch: vi.fn(), ...extra };
}

const state = vi.hoisted(() => ({ hooks: {} as Record<string, unknown> }));
const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
const clientMocks = vi.hoisted(() => ({ extractEntity: vi.fn() }));

vi.mock("sonner", () => ({ toast: toastMock }));

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

vi.mock("@/lib/studio/hooks", () => ({
  useDagGraph: () => state.hooks.useDagGraph,
  useRefreshDataset: () => state.hooks.useRefreshDataset,
  useCartridgeStatus: () => state.hooks.useCartridgeStatus,
}));

vi.mock("@/lib/monitor/hooks", () => ({
  useDatasets: () => state.hooks.useDatasets,
  useDatasetDetail: () => state.hooks.useDatasetDetail,
  usePipeline: () => state.hooks.usePipeline,
  useSourceSchema: () => state.hooks.useSourceSchema,
  useEntityRuns: () => state.hooks.useEntityRuns,
  useEntityRunLogs: () => query(undefined),
  useJob: () => query(undefined),
  useJobLogs: () => query([]),
  findEntityRun: (runs: Array<{ dag_run_id?: string }> | undefined, id: string) => runs?.find((run) => run.dag_run_id === id),
  isTerminalRunStatus: (status?: string) => status === "success",
}));

vi.mock("@/lib/hooks/useAnalyticsApps", () => ({ useAnalyticsApps: () => state.hooks.useAnalyticsApps }));
vi.mock("@/lib/monitor/client", () => ({ extractEntity: clientMocks.extractEntity }));

const MANIFEST = {
  id: "acme",
  name: "Acme ERP",
  description: "ERP de pruebas",
  dags: [{ dag_id: "acme_invoice", description: "Extrae las facturas" }],
  entities: [
    {
      entity: "Invoice",
      display_name: "Facturas",
      description: "Facturas emitidas a clientes",
      dag_id: "acme_invoice",
      trigger_type: "scheduled",
      cron_expression: "0 8 * * *",
      mode: "incremental",
    },
  ],
};

const COLUMNS = Array.from({ length: 14 }, (_, index) => ({ name: `col_${index + 1}`, type: "VARCHAR", description: `Columna ${index + 1}` }));

function resetHooks() {
  state.hooks = {
    useDagGraph: query({
      format: "svg",
      nodes: [
        { id: "cartridge:acme", kind: "cartridge", label: "Acme ERP" },
        { id: "entity:Invoice", kind: "entity", label: "Invoice", layer: "bronze" },
        { id: "dag:acme_invoice", kind: "dag", label: "acme_invoice" },
        { id: "dataset:silver:orders", kind: "dataset", label: "silver:orders", layer: "silver" },
        { id: "dataset:gold:sales", kind: "dataset", label: "gold:sales", layer: "gold" },
      ],
      edges: [
        { source: "cartridge:acme", target: "entity:Invoice" },
        { source: "entity:Invoice", target: "dag:acme_invoice" },
        { source: "entity:Invoice", target: "dataset:silver:orders" },
        { source: "dataset:silver:orders", target: "dataset:gold:sales" },
      ],
    }),
    useRefreshDataset: { mutate: vi.fn(), isPending: false } satisfies Mutation,
    useCartridgeStatus: query({ cartridge_id: "acme", status: "degraded", detail: "latencia alta" }),
    useDatasets: query([
      { name: "orders", layer: "silver", cartridge: "acme", row_count: 30, is_stale: false, last_refresh: "2026-09-26T10:00:00Z" },
      { name: "sales", layer: "gold", cartridge: "acme", row_count: 12, is_stale: false, last_refresh: "2026-09-26T10:30:00Z" },
    ]),
    useDatasetDetail: query({
      name: "orders",
      layer: "silver",
      sql: "select 1",
      columns: COLUMNS,
      metadata: { description: "Pedidos limpios", schedule: null },
      status: "ok",
    }),
    usePipeline: query([
      {
        entity: "Invoice",
        cartridge: "acme",
        modes: ["incremental"],
        last_run: { status: "success", finished_at: "2026-09-26T11:00:00Z" },
        bronze: { source: "raw/acme/Invoice", record_count: 1200, status: "ok" },
        silver: [],
        gold: [],
      },
    ]),
    useSourceSchema: query({ status: "ok", preview: { schema: [{ name: "DocEntry", type: "BIGINT" }] } }),
    useEntityRuns: query([{ dag_run_id: "run-9", status: "running" }]),
    useAnalyticsApps: query({
      apps: [
        { name: "tablero_ventas", title: "Tablero de ventas", datasets_used: ["sales"] },
        { name: "otra_app", title: "Otra app", datasets_used: ["unrelated"] },
      ],
    }),
  };
}

let container: HTMLDivElement;
let root: Root;
const onOpenEditor = vi.fn();
const onOpenSection = vi.fn();

async function render() {
  await act(async () => {
    root.render(
      <DagGraph cartridge="acme" manifest={MANIFEST} onOpenEditor={onOpenEditor} onOpenSection={onOpenSection} />,
    );
  });
}

function byText(selector: string, text: string | RegExp): HTMLElement | undefined {
  return [...document.querySelectorAll<HTMLElement>(selector)].find((element) => {
    const content = element.textContent?.trim() ?? "";
    return typeof text === "string" ? content === text : text.test(content);
  });
}

async function click(element: Element | null | undefined) {
  expect(element, "element to click").toBeTruthy();
  await act(async () => {
    element?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

async function dispatch(element: Element | null | undefined, event: Event) {
  expect(element, "event target").toBeTruthy();
  await act(async () => {
    element?.dispatchEvent(event);
  });
}

async function key(element: Element | null | undefined, value: string) {
  await dispatch(element, new KeyboardEvent("keydown", { key: value, bubbles: true, cancelable: true }));
}

async function flush() {
  for (let tick = 0; tick < 4; tick += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

const graph = () => container.querySelector('[data-testid="dag-graph"]');
const zoom = () => container.querySelector('[data-testid="dag-graph-zoom"]')?.textContent;
const viewport = () => container.querySelector('[data-testid="dag-graph-viewport"]')?.getAttribute("transform") ?? "";
const drawer = () => container.querySelector('[data-testid="dag-graph-detail"]');
const node = (id: string) => container.querySelector(`[data-node-id="${id}"]`);

async function openNode(id: string, tab?: string) {
  await click(node(id));
  if (tab) await click(byText('[data-testid="dag-graph-detail"] [role="tab"]', tab));
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  resetHooks();
  vi.clearAllMocks();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("DagGraph canvas", () => {
  it("keeps one node per node and one arrow per edge, lighting the hovered node's edges", async () => {
    await render();
    expect(graph()?.querySelectorAll("[data-node-id]")).toHaveLength(5);
    expect(graph()?.querySelectorAll("path[marker-end]")).toHaveLength(4);
    expect(container.querySelectorAll("[data-node-id]")).toHaveLength(5);
    expect(container.querySelectorAll("path[marker-end]")).toHaveLength(4);

    await dispatch(node("entity:Invoice"), new MouseEvent("mouseover", { bubbles: true }));
    expect(graph()?.querySelectorAll("path[marker-end]")).toHaveLength(4);
    const active = [...(graph()?.querySelectorAll('path[data-active="true"]') ?? [])];
    expect(active).toHaveLength(3);
    expect(active.map((path) => path.getAttribute("data-direction")).sort()).toEqual(["in", "out", "out"]);

    await dispatch(node("entity:Invoice"), new MouseEvent("mouseout", { bubbles: true }));
    expect(graph()?.querySelectorAll('path[data-active="true"]')).toHaveLength(0);
  });

  it("labels columns by stage and shows the legend with real totals", async () => {
    await render();
    const labels = [...(graph()?.querySelectorAll("text") ?? [])].map((text) => text.textContent);
    expect(labels).toEqual(expect.arrayContaining(["Conector", "Tablas de origen", "Automatizaciones · Plata", "Oro"]));
    const legend = container.querySelector('[aria-label="Leyenda"]');
    expect(legend?.textContent).toContain("5 nodos · 4 relaciones");
    expect(node("dataset:gold:sales")?.getAttribute("data-stage")).toBe("oro");
    expect(node("entity:Invoice")?.querySelector("title")?.textContent).toContain("1 entrada · 2 salidas");
  });

  it("zooms with the wheel between 40 % and 250 %", async () => {
    await render();
    expect(zoom()).toBe("100 %");
    for (let step = 0; step < 10; step += 1) {
      await dispatch(graph(), new WheelEvent("wheel", { deltaY: -1000, bubbles: true, cancelable: true }));
    }
    expect(zoom()).toBe("250 %");
    expect(viewport()).toContain("scale(2.5)");
    for (let step = 0; step < 20; step += 1) {
      await dispatch(graph(), new WheelEvent("wheel", { deltaY: 1000, bubbles: true, cancelable: true }));
    }
    expect(zoom()).toBe("40 %");
    expect(viewport()).toContain("scale(0.4)");
  });

  it("zooms with the glass controls and disables them at the bounds", async () => {
    await render();
    await click(container.querySelector('button[aria-label="Acercar"]'));
    expect(zoom()).toBe("120 %");
    await click(container.querySelector('button[aria-label="Restablecer al 100 %"]'));
    expect(zoom()).toBe("100 %");
    const zoomOut = container.querySelector('button[aria-label="Alejar"]');
    for (let step = 0; step < 6; step += 1) await click(zoomOut);
    expect(zoom()).toBe("40 %");
    expect(zoomOut?.hasAttribute("disabled")).toBe(true);
    await click(container.querySelector('button[aria-label="Centrar y ajustar"]'));
    expect(zoom()).toBe("100 %");
    const zoomIn = container.querySelector('button[aria-label="Acercar"]');
    for (let step = 0; step < 6; step += 1) await click(zoomIn);
    expect(zoom()).toBe("250 %");
    expect(zoomIn?.hasAttribute("disabled")).toBe(true);
  });

  it("answers keyboard shortcuts only when the canvas itself has focus", async () => {
    await render();
    const canvas = container.querySelector('[role="region"][aria-label="Lienzo del Mapa del Flujo"]');
    expect(canvas?.getAttribute("tabindex")).toBe("0");
    await key(canvas, "+");
    expect(zoom()).toBe("120 %");
    await key(canvas, "0");
    expect(zoom()).toBe("100 %");
    await key(canvas, "ArrowRight");
    expect(viewport()).toBe("translate(-48 0) scale(1)");
    await key(canvas, "ArrowDown");
    expect(viewport()).toBe("translate(-48 -48) scale(1)");
    await key(canvas, "f");
    expect(viewport()).toBe("translate(0 0) scale(1)");
    await key(node("entity:Invoice"), "+");
    expect(zoom()).toBe("100 %");
  });

  it("pans by dragging the background", async () => {
    await render();
    const background = container.querySelector("rect[data-canvas-bg]");
    await dispatch(background, new MouseEvent("pointerdown", { bubbles: true, clientX: 100, clientY: 100, button: 0 }));
    await dispatch(graph(), new MouseEvent("pointermove", { bubbles: true, clientX: 160, clientY: 130 }));
    expect(viewport()).toBe("translate(60 30) scale(1)");
    await dispatch(graph(), new MouseEvent("pointerup", { bubbles: true, clientX: 160, clientY: 130 }));
    await dispatch(graph(), new MouseEvent("pointermove", { bubbles: true, clientX: 300, clientY: 300 }));
    expect(viewport()).toBe("translate(60 30) scale(1)");
  });

  it("marks only ready gold datasets with the pulse dot", async () => {
    await render();
    expect(graph()?.querySelectorAll('[data-status-dot="ready"]')).toHaveLength(1);
    expect(node("dataset:gold:sales")?.querySelector('[data-status-dot="ready"]')).toBeTruthy();

    await act(async () => root.unmount());
    root = createRoot(container);
    state.hooks.useDatasets = query([{ name: "sales", layer: "gold", is_stale: true, staleness_reason: "cambió la fuente" }]);
    await render();
    const stale = graph()?.querySelector('[data-status-dot="stale"]');
    expect(stale?.querySelector("title")?.textContent).toBe("Desactualizado: cambió la fuente");
    expect(graph()?.querySelectorAll('[data-status-dot="ready"]')).toHaveLength(0);

    await act(async () => root.unmount());
    root = createRoot(container);
    state.hooks.useDatasets = query([{ name: "sales", layer: "gold", is_stale: false }]);
    await render();
    expect(graph()?.querySelectorAll("[data-status-dot]")).toHaveLength(0);
  });
});

describe("DagGraph node drawer", () => {
  it("describes a source table from the manifest and the pipeline", async () => {
    await render();
    await openNode("entity:Invoice");
    const detail = drawer();
    expect(detail?.getAttribute("role")).toBe("dialog");
    expect(detail?.querySelector("h2")?.textContent).toBe("Facturas");
    expect(detail?.textContent).toContain("Entradas (1) · Salidas (2)");
    expect(detail?.textContent).toContain("Facturas emitidas a clientes");
    expect(detail?.textContent).toContain("Diaria a las 08:00 UTC (cron 0 8 * * *)");
    expect(detail?.textContent).toContain("1,200 registros");
    expect(detail?.querySelector("[data-node-status]")?.textContent).toBe("Sincronizado");
    expect(detail?.querySelector('time[dateTime="2026-09-26T11:00:00Z"]')).toBeTruthy();
    expect(container.querySelector('[role="group"][aria-label="Controles del lienzo"]')?.className).toContain("md:right-[436px]");
  });

  it("says so when a fact is missing instead of inventing it", async () => {
    state.hooks.usePipeline = query([]);
    await render();
    await openNode("dag:acme_invoice");
    const detail = drawer();
    expect(detail?.textContent).toContain("Extrae las facturas");
    expect(detail?.textContent).toContain("1 tabla de origen");
    expect(detail?.textContent).toContain("Sin información de estado");
    expect(detail?.textContent).toContain("Sin fecha registrada.");

    await openNode("dataset:silver:orders");
    expect(drawer()?.textContent).toContain("Pedidos limpios");
    expect(drawer()?.textContent).toContain("Sin programación registrada.");
    expect(drawer()?.textContent).toContain("30 registros");
    expect(drawer()?.querySelector("[data-node-status]")?.textContent).toBe("Sincronizado");
  });

  it("shows the connector's probe without duplicating the page's status test id", async () => {
    await render();
    await openNode("cartridge:acme");
    expect(drawer()?.querySelector('[data-testid="node-cartridge-status"]')?.getAttribute("data-status")).toBe("degraded");
    expect(drawer()?.querySelector('[data-testid="cartridge-status"]')).toBeNull();
    expect(drawer()?.textContent).toContain("1 tabla");
  });

  it("lists who feeds and uses a dataset and the analytic apps that depend on it", async () => {
    await render();
    await openNode("dataset:silver:orders", "¿Quién lo alimenta y quién lo usa?");
    const detail = drawer();
    expect(detail?.textContent).toContain("Lo alimentan (1)");
    expect(byText('[data-testid="dag-graph-detail"] button', /^Facturas/)).toBeTruthy();
    expect(detail?.textContent).toContain("Lo usan (1)");
    const impact = detail?.querySelector('[data-testid="lineage-impact"]');
    expect(impact?.textContent).toContain("1 app analítica depende de este dato");
    const link = [...(detail?.querySelectorAll("a") ?? [])].find((anchor) => anchor.textContent?.includes("Tablero de ventas"));
    expect(link?.getAttribute("href")).toBe("/analytics/viewer?app=tablero_ventas");
    expect(detail?.textContent).not.toContain("Otra app");

    await click(byText('[data-testid="dag-graph-detail"] button', /^Facturas/));
    expect(drawer()?.querySelector("h2")?.textContent).toBe("Facturas");
    expect(node("entity:Invoice")?.getAttribute("aria-pressed")).toBe("true");
  });

  it("reports when the apps catalog cannot be read", async () => {
    state.hooks.useAnalyticsApps = query(undefined, { isError: true, isSuccess: false, error: new Error("boom") });
    await render();
    await openNode("dataset:gold:sales", "¿Quién lo alimenta y quién lo usa?");
    expect(drawer()?.querySelector('[role="alert"]')).toBeTruthy();
  });

  it("opens the query editor and forces a dataset refresh only after confirming", async () => {
    const refresh = state.hooks.useRefreshDataset as Mutation;
    refresh.mutate.mockImplementation((_name: string, options: { onSuccess?: (result: unknown) => void; onSettled?: () => void }) => {
      options.onSuccess?.({ row_count: 31 });
      options.onSettled?.();
    });
    await render();
    await openNode("dataset:silver:orders", "Ver información");
    expect(drawer()?.textContent).toContain("Primeras 12 columnas de 14");
    expect(drawer()?.querySelectorAll("tbody tr")).toHaveLength(12);
    const showAll = byText('[data-testid="dag-graph-detail"] button', "Mostrar todas");
    await click(showAll);
    expect(drawer()?.querySelectorAll("tbody tr")).toHaveLength(14);

    await click(byText('[data-testid="dag-graph-detail"] button', /Abrir en Editor de Consultas/));
    expect(onOpenEditor).toHaveBeenCalledWith({ dataset: "orders" });

    await click(byText('[data-testid="dag-graph-detail"] button', /Forzar actualización ahora/));
    expect(document.querySelector('[data-testid="refresh-dataset-dialog"]')).toBeTruthy();
    expect(drawer()?.querySelector('[data-testid="refresh-dataset-dialog"]')).toBeNull();
    await key(document.activeElement, "Escape");
    expect(document.querySelector('[data-testid="refresh-dataset-dialog"]')).toBeNull();
    expect(drawer()).toBeTruthy();
    expect(refresh.mutate).not.toHaveBeenCalled();

    await click(byText('[data-testid="dag-graph-detail"] button', /Forzar actualización ahora/));
    await click(byText('[data-testid="refresh-dataset-dialog"] button', "Actualizar ahora"));
    expect(refresh.mutate.mock.calls[0][0]).toBe("orders");
    expect(toastMock.success).toHaveBeenCalledWith("Dataset orders materializado: 31 filas.");
    expect(document.querySelector('[data-testid="refresh-dataset-dialog"]')).toBeNull();
  });

  it("forces an extraction for a source table and follows it", async () => {
    clientMocks.extractEntity.mockResolvedValue({ dag_id: "acme_invoice", dag_run_id: "run-9" });
    await render();
    await openNode("entity:Invoice", "Ver información");
    expect(drawer()?.textContent).toContain("DocEntry");
    await click(byText('[data-testid="dag-graph-detail"] button', /Abrir en Editor de Consultas/));
    expect(onOpenEditor).toHaveBeenCalledWith({ entity: "Invoice" });

    await click(byText('[data-testid="dag-graph-detail"] button', /Forzar actualización ahora/));
    const dialog = document.querySelector('[data-testid="extract-entity-dialog"]');
    expect(dialog?.textContent).toContain("incremental de Invoice desde Acme ERP");
    await click(byText('[data-testid="extract-entity-dialog"] button', "Lanzar extracción"));
    await flush();
    expect(clientMocks.extractEntity).toHaveBeenCalledWith("acme", "Invoice", { mode: "incremental" });
    const tracker = drawer()?.querySelector('[data-testid="extraction-tracker"]');
    expect(tracker?.textContent).toContain("run-9");
    expect(toastMock.success).toHaveBeenCalledWith("Extracción enviada para Invoice.");
  });

  it("sends DAG nodes to Automatizaciones", async () => {
    await render();
    await openNode("dag:acme_invoice", "Ver información");
    expect(drawer()?.textContent).toContain("Extrae 1 tabla de origen: Invoice.");
    await click(byText('[data-testid="dag-graph-detail"] button', /Abrir en Automatizaciones/));
    expect(onOpenSection).toHaveBeenCalledWith("dags");
  });

  it("moves focus into the drawer, closes with Escape and returns focus to the node", async () => {
    await render();
    await openNode("dataset:silver:orders");
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Cerrar detalle");
    const tabs = [...(drawer()?.querySelectorAll('[role="tab"]') ?? [])];
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "¿De qué se trata este dato?",
      "¿Quién lo alimenta y quién lo usa?",
      "Ver información",
    ]);
    await key(tabs[0], "ArrowRight");
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tabs[1]);

    await dispatch(drawer(), new WheelEvent("wheel", { deltaY: -1000, bubbles: true, cancelable: true }));
    expect(zoom()).toBe("100 %");

    await key(document.body, "Escape");
    expect(drawer()).toBeNull();
    expect(document.activeElement).toBe(node("dataset:silver:orders"));

    await openNode("entity:Invoice");
    await click(container.querySelector('button[aria-label="Cerrar detalle"]'));
    expect(drawer()).toBeNull();
    expect(document.activeElement).toBe(node("entity:Invoice"));
  });
});
