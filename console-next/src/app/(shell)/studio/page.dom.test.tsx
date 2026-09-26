// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { toApiError } from "@/lib/api";

import StudioPage from "./page";

type Mutation = {
  mutate: ReturnType<typeof vi.fn>;
  isPending: boolean;
};

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

function mutation(): Mutation {
  return { mutate: vi.fn(), isPending: false };
}

const state = vi.hoisted(() => ({
  hooks: {} as Record<string, unknown>,
}));

const toastMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}));

vi.mock("sonner", () => ({ toast: toastMock }));

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

vi.mock("@/lib/studio/hooks", () => {
  const pick = (name: string) => () => state.hooks[name];
  return {
    useStudioCartridges: pick("useStudioCartridges"),
    useStudioManifest: pick("useStudioManifest"),
    useCartridgeStatus: pick("useCartridgeStatus"),
    useCreateCartridge: pick("useCreateCartridge"),
    useImportCartridge: pick("useImportCartridge"),
    useDagGraph: pick("useDagGraph"),
    useStudioDags: pick("useStudioDags"),
    useDagSource: pick("useDagSource"),
    useDagTemplates: pick("useDagTemplates"),
    useSystemInfo: pick("useSystemInfo"),
    useRuntimeConfig: pick("useRuntimeConfig"),
    useDeployDag: pick("useDeployDag"),
    useRenameDag: pick("useRenameDag"),
    useDeleteDag: pick("useDeleteDag"),
    useStudioEntities: pick("useStudioEntities"),
    useUpdateEntity: pick("useUpdateEntity"),
    useRenameEntity: pick("useRenameEntity"),
    useCreateEntity: pick("useCreateEntity"),
    useUploadEntitySpec: pick("useUploadEntitySpec"),
    useIntrospectSource: pick("useIntrospectSource"),
    useLayerPreview: pick("useLayerPreview"),
    usePublishSuperset: pick("usePublishSuperset"),
    useSaveDataset: pick("useSaveDataset"),
    useRefreshDataset: pick("useRefreshDataset"),
    useDeleteDataset: pick("useDeleteDataset"),
  };
});

vi.mock("@/lib/monitor/hooks", () => ({
  useDatasets: () => state.hooks.useDatasets,
  useDatasetDetail: () => state.hooks.useDatasetDetail,
  usePipeline: () => state.hooks.usePipeline,
  useSourceSchema: () => state.hooks.useSourceSchema,
  useEntityRuns: () => state.hooks.useEntityRuns,
  useEntityRunLogs: () => state.hooks.useEntityRunLogs,
  useJob: () => state.hooks.useJob,
  useJobLogs: () => state.hooks.useJobLogs,
  findEntityRun: () => undefined,
  isTerminalRunStatus: () => false,
}));

vi.mock("@/lib/hooks/useAnalyticsApps", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/hooks/useAnalyticsApps")>()),
  useAnalyticsApps: () => state.hooks.useAnalyticsApps,
}));

const MANIFEST = {
  id: "acme",
  name: "Acme ERP",
  dags: [{ dag_id: "acme_packaged" }],
  entities: [{ entity: "Invoice", name: "Invoice", dag_id: "acme_packaged" }],
};

function resetHooks() {
  state.hooks = {
    useStudioCartridges: query([
      { id: "acme", name: "Acme ERP" },
      { id: "beta", name: "Beta" },
    ]),
    useStudioManifest: query(MANIFEST),
    useCartridgeStatus: query({ cartridge_id: "acme", status: "registered", detail: "sin servicio propio que sondear" }),
    useCreateCartridge: mutation(),
    useImportCartridge: mutation(),
    useDagGraph: query({
      format: "svg",
      svg: '<svg><script data-injected="yes"></script></svg>',
      nodes: [
        { id: "cartridge:acme", kind: "cartridge", label: "Acme ERP" },
        { id: "entity:Invoice", kind: "entity", label: "Invoice" },
        { id: "dag:acme_packaged", kind: "dag", label: "acme_packaged" },
      ],
      edges: [
        { source: "cartridge:acme", target: "entity:Invoice" },
        { source: "entity:Invoice", target: "dag:acme_packaged" },
      ],
    }),
    useStudioDags: query({
      cartridge: "acme",
      total: 2,
      dags: [
        { dag_id: "acme_custom", is_paused: false, is_active: true, tags: [] },
        { dag_id: "acme_packaged", is_paused: false, is_active: false, registered_only: true, tags: [] },
      ],
    }),
    useDagSource: query({ dag_id: "acme_custom", found: true, source_code: "dag_id='acme_custom'\n", path: null }),
    useDagTemplates: query([{ id: "full_extract", name: "Extracción completa" }]),
    useSystemInfo: query({ dev_mode: true, rce_tools_enabled: true, dag_deploy_enabled: true }),
    useRuntimeConfig: query({ airflow_url: "http://airflow.test:8082", superset_url: null }),
    useDeployDag: mutation(),
    useRenameDag: mutation(),
    useDeleteDag: mutation(),
    useStudioEntities: query({ cartridge: "acme", total: 0, entities: [] }),
    useUpdateEntity: mutation(),
    useRenameEntity: mutation(),
    useCreateEntity: mutation(),
    useUploadEntitySpec: mutation(),
    useIntrospectSource: { ...mutation(), data: undefined },
    useLayerPreview: query(undefined),
    usePublishSuperset: mutation(),
    useSaveDataset: mutation(),
    useRefreshDataset: mutation(),
    useDeleteDataset: mutation(),
    useDatasets: query([]),
    useDatasetDetail: query(undefined),
    usePipeline: query([]),
    useAnalyticsApps: query({ apps: [] }),
    useSourceSchema: query(undefined),
    useEntityRuns: query([]),
    useEntityRunLogs: query(undefined),
    useJob: query(undefined),
    useJobLogs: query([]),
  };
}

let container: HTMLDivElement;
let root: Root;

async function render() {
  await act(async () => {
    root.render(
      <QueryClientProvider client={new QueryClient()}>
        <StudioPage />
      </QueryClientProvider>,
    );
  });
}

function byText(selector: string, text: string | RegExp): HTMLElement | undefined {
  return [...container.querySelectorAll<HTMLElement>(selector)].find((element) => {
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

async function openDagsTab() {
  await click(container.querySelector("#studio-tab-dags"));
}

async function keyDown(element: Element | null | undefined, key: string) {
  expect(element, "element for keydown").toBeTruthy();
  await act(async () => {
    element?.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
  });
}

function accessibleText(element: Element): string {
  const clone = element.cloneNode(true) as Element;
  clone.querySelectorAll('[aria-hidden="true"], [aria-hidden=""]').forEach((node) => node.remove());
  return clone.textContent?.trim() ?? "";
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.history.replaceState(null, "", "/");
  Element.prototype.scrollIntoView = vi.fn();
  resetHooks();
  vi.clearAllMocks();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("Studio page", () => {
  it("renders the cartridge selector from /studio/cartridges and the honest probe status", async () => {
    await render();
    const picker = container.querySelector<HTMLSelectElement>('[data-testid="cartridge-picker"]');
    expect(picker?.value).toBe("acme");
    expect([...(picker?.options ?? [])].map((option) => option.value)).toEqual(["acme", "beta"]);
    const status = container.querySelector('[data-testid="cartridge-status"]');
    expect(status?.getAttribute("data-status")).toBe("registered");
    expect(status?.textContent).toContain("sin servicio propio que sondear");
    expect(container.textContent).not.toContain("Operativo");
  });

  it("draws the graph from nodes/edges and never injects the server svg", async () => {
    await render();
    const graph = container.querySelector('[data-testid="dag-graph"]');
    expect(graph).toBeTruthy();
    expect(graph?.querySelectorAll("[data-node-id]")).toHaveLength(3);
    expect(graph?.querySelectorAll("path[marker-end]")).toHaveLength(2);
    expect(container.querySelector('[data-injected="yes"]')).toBeNull();
    expect(container.innerHTML).not.toContain("<script");

    const invoice = container.querySelector('[data-node-id="entity:Invoice"]');
    await act(async () => {
      invoice?.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    });
    expect(graph?.querySelectorAll("[data-node-id]")).toHaveLength(3);
    expect(graph?.querySelectorAll("path[marker-end]")).toHaveLength(2);

    await click(invoice);
    const detail = container.querySelector('[role="dialog"][data-testid="dag-graph-detail"]');
    expect(detail?.textContent).toContain("Invoice");
    expect(detail?.textContent).toContain("Entradas (1)");
    expect(detail?.textContent).toContain("Salidas (1)");
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Cerrar detalle");

    await keyDown(document.activeElement, "Escape");
    expect(container.querySelector('[data-testid="dag-graph-detail"]')).toBeNull();
    expect(document.activeElement).toBe(container.querySelector('[data-node-id="entity:Invoice"]'));
  });

  it("names the sections for the business with real counts and hints", async () => {
    await render();
    const tabs = [...container.querySelectorAll('[role="tab"][id^="studio-tab-"]')];
    expect(tabs.map((tab) => tab.querySelector("[data-tab-label]")?.textContent)).toEqual([
      "Mapa del Flujo",
      "Automatizaciones",
      "Tablas de Origen (Bronce)",
      "Modelado y Limpieza (Plata)",
      "Indicadores y KPIs (Oro)",
    ]);
    expect(tabs.map((tab) => tab.querySelector("[data-tab-count]")?.textContent)).toEqual(["3", "1", "1", "0", "0"]);
    expect(tabs.map(accessibleText)).toEqual([
      "Mapa del Flujo",
      "Automatizaciones",
      "Tablas de Origen (Bronce)",
      "Modelado y Limpieza (Plata)",
      "Indicadores y KPIs (Oro)",
    ]);
    expect(container.querySelector("#studio-tab-hint-entidades")?.textContent).toContain(
      "Datos crudos extraídos directamente de Acme ERP",
    );
    expect(container.querySelector("#studio-tab-hint-entidades")?.textContent).toContain("1 tabla de origen");
    expect(container.querySelector("#studio-tab-entidades")?.getAttribute("aria-describedby")).toBe(
      "studio-tab-hint-entidades",
    );
    expect(container.querySelector("#studio-section-hint")?.textContent).toBe(
      "Vista visual de cómo viaja la información desde el origen hasta los reportes finales",
    );
  });

  it("shows the cartridge health only from real fields", async () => {
    await render();
    const health = container.querySelector('[data-testid="studio-health"]');
    expect(health?.getAttribute("aria-label")).toBe("Salud del cartucho");
    expect(health?.querySelector('[data-metric="tables"] dd')?.textContent).toBe("1");
    expect(health?.querySelector('[data-metric="gold-ready"] dd')?.textContent).toBe("Sin datasets Oro");
    expect(health?.querySelector('[data-metric="last-refresh"]')).toBeNull();

    await act(async () => root.unmount());
    root = createRoot(container);
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-26T12:00:00Z"));
    state.hooks.useDatasets = query([
      { name: "ventas", layer: "gold", cartridge: "acme", is_stale: false, last_refresh: "2026-09-26T11:48:00Z" },
      { name: "margen", layer: "gold", cartridge: "acme", is_stale: true },
      { name: "x", layer: "gold", cartridge: "beta", is_stale: false, last_refresh: "2026-09-26T11:59:00Z" },
    ]);
    await render();
    const refreshed = container.querySelector('[data-testid="studio-health"]');
    expect(refreshed?.querySelector('[data-metric="gold-ready"] dd')?.textContent).toBe("1 de 2");
    const time = refreshed?.querySelector('[data-metric="last-refresh"] time');
    expect(time?.textContent).toBe("hace 12 minutos");
    expect(time?.getAttribute("dateTime")).toBe("2026-09-26T11:48:00Z");
    expect(container.querySelector("#studio-tab-capas [data-tab-count]")?.textContent).toBe("2");
  });

  it("Desplegar a Airflow opens Automatizaciones and focuses it without deploying", async () => {
    const deploy = state.hooks.useDeployDag as Mutation;
    await render();
    const button = byText("button", /Desplegar a Airflow/);
    expect(button?.getAttribute("aria-describedby")).toBe("studio-deploy-hint");
    await click(button);
    expect(container.querySelector("#studio-tab-dags")?.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement?.id).toBe("studio-panel-dags");
    expect(deploy.mutate).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="deploy-dialog"]')).toBeNull();
    expect(new URLSearchParams(window.location.search).get("tab")).toBe("dags");
  });

  it("opens the Studio assistant with the section's suggested questions", async () => {
    await render();
    const toggle = byText("button", /Consultar al Asistente de Studio/);
    expect(toggle?.getAttribute("aria-expanded")).toBe("false");
    expect([...container.querySelectorAll("button")].filter((button) => /Asistente de Studio/.test(button.textContent ?? ""))).toHaveLength(1);
    await click(toggle);
    expect(toggle?.getAttribute("aria-expanded")).toBe("true");
    const assistant = container.querySelector('[data-testid="studio-assistant"]');
    expect(assistant).toBeTruthy();
    expect(assistant?.textContent).toContain("Sección: Mapa del Flujo");
    expect(container.querySelectorAll('[aria-label="Preguntas sugeridas"] button')).toHaveLength(2);
  });

  it("moves between sections with the arrow keys", async () => {
    await render();
    const first = container.querySelector<HTMLElement>("#studio-tab-grafo");
    expect(first?.getAttribute("tabindex")).toBe("0");
    await keyDown(first, "ArrowRight");
    const dags = container.querySelector("#studio-tab-dags");
    expect(dags?.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(dags);
    await keyDown(dags, "End");
    expect(document.activeElement?.id).toBe("studio-tab-capas");
    await keyDown(document.activeElement, "Home");
    expect(document.activeElement?.id).toBe("studio-tab-grafo");
  });

  it("opens a dataset from the flow map in the query editor", async () => {
    state.hooks.useDagGraph = query({
      nodes: [
        { id: "cartridge:acme", kind: "cartridge", label: "Acme ERP" },
        { id: "entity:Invoice", kind: "entity", label: "Invoice", layer: "bronze" },
        { id: "dataset:silver:orders", kind: "dataset", label: "silver:orders", layer: "silver" },
      ],
      edges: [
        { source: "cartridge:acme", target: "entity:Invoice" },
        { source: "entity:Invoice", target: "dataset:silver:orders" },
      ],
    });
    state.hooks.useDatasetDetail = query({
      name: "orders",
      layer: "silver",
      sql: "select * from invoices",
      columns: [{ name: "id", type: "BIGINT" }],
      metadata: { description: "Pedidos" },
    });
    await render();
    await click(container.querySelector('[data-node-id="dataset:silver:orders"]'));
    await click(byText('[data-testid="dag-graph-detail"] [role="tab"]', "Ver información"));
    await click(byText('[data-testid="dag-graph-detail"] button', /Abrir en Editor de Consultas/));
    expect(container.querySelector("#studio-tab-refinar")?.getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector<HTMLTextAreaElement>('textarea[name="sql"]')?.value).toBe("select * from invoices");
    expect(new URLSearchParams(window.location.search).get("tab")).toBe("refinar");

    await click(container.querySelector("#studio-tab-grafo"));
    await click(container.querySelector("#studio-tab-refinar"));
    expect(container.querySelector('textarea[name="sql"]')).toBeNull();
    expect(container.textContent).toContain("Selecciona un dataset o crea uno nuevo");
  });

  it("confirms before deploying and toasts the deployed outcome", async () => {
    const deploy = state.hooks.useDeployDag as Mutation;
    deploy.mutate.mockImplementation((input: { dag_id: string }, options: {
      onSuccess?: (result: unknown) => void;
      onSettled?: () => void;
    }) => {
      options.onSuccess?.({ status: "deployed", dag_id: input.dag_id });
      options.onSettled?.();
    });
    await render();
    await openDagsTab();
    await click(container.querySelector('[data-dag-id="acme_custom"]'));

    const airflow = container.querySelector<HTMLAnchorElement>('[data-testid="dag-airflow-link"]');
    expect(airflow?.getAttribute("href")).toBe("http://airflow.test:8082/dags/acme_custom/grid");
    expect(airflow?.getAttribute("target")).toBe("_blank");
    expect(airflow?.getAttribute("rel")).toContain("noopener");

    await click(byText("button", /Deploy a Airflow/));
    expect(deploy.mutate).not.toHaveBeenCalled();
    const dialog = container.querySelector('[data-testid="deploy-dialog"]');
    expect(dialog?.getAttribute("role")).toBe("dialog");
    expect(dialog?.textContent).toContain("acme_custom");

    await click(byText('[data-testid="deploy-dialog"] button', "Desplegar"));
    expect(deploy.mutate).toHaveBeenCalledTimes(1);
    expect(deploy.mutate.mock.calls[0][0]).toMatchObject({
      cartridge: "acme",
      entity: "Invoice",
      dag_id: "acme_custom",
      code: "dag_id='acme_custom'\n",
    });
    expect(toastMock.success).toHaveBeenCalledWith("DAG acme_custom desplegado en Airflow.");
    expect(container.querySelector('[data-testid="deploy-dialog"]')).toBeNull();
  });

  it("cancelling the deploy modal never calls the mutation", async () => {
    const deploy = state.hooks.useDeployDag as Mutation;
    await render();
    await openDagsTab();
    await click(container.querySelector('[data-dag-id="acme_custom"]'));
    await click(byText("button", /Deploy a Airflow/));
    await click(byText('[data-testid="deploy-dialog"] button', "Cancelar"));
    expect(container.querySelector('[data-testid="deploy-dialog"]')).toBeNull();
    expect(deploy.mutate).not.toHaveBeenCalled();
  });

  it("toasts managed and failed deploy outcomes distinctly", async () => {
    const deploy = state.hooks.useDeployDag as Mutation;
    deploy.mutate.mockImplementationOnce((_input: unknown, options: { onSuccess?: (result: unknown) => void }) => {
      options.onSuccess?.({ status: "failed", dag_id: "acme_custom", error: "SyntaxError en línea 3" });
    });
    await render();
    await openDagsTab();
    await click(container.querySelector('[data-dag-id="acme_custom"]'));
    await click(byText("button", /Deploy a Airflow/));
    await click(byText('[data-testid="deploy-dialog"] button', "Desplegar"));
    expect(toastMock.error).toHaveBeenCalledWith("No se pudo desplegar acme_custom: SyntaxError en línea 3");
  });

  it("disables deploy with a visible reason for packaged DAGs and when the environment forbids it", async () => {
    await render();
    await openDagsTab();
    await click(container.querySelector('[data-dag-id="acme_packaged"]'));
    expect(byText("button", /Deploy a Airflow/)?.hasAttribute("disabled")).toBe(true);
    expect(container.querySelector('[data-testid="deploy-disabled-reason"]')?.textContent).toContain(
      "DAG empaquetado por el cartucho",
    );
    expect(container.textContent).toContain("Solo manifiesto");
    expect(byText('[data-dag-id="acme_packaged"] span', "Inactivo")).toBeTruthy();

    await act(async () => root.unmount());
    root = createRoot(container);
    state.hooks.useSystemInfo = query({ dev_mode: false, rce_tools_enabled: false, dag_deploy_enabled: false });
    await render();
    await openDagsTab();
    await click(container.querySelector('[data-dag-id="acme_custom"]'));
    const deployButton = byText("button", /Deploy a Airflow/);
    expect(deployButton?.hasAttribute("disabled")).toBe(true);
    expect(deployButton?.getAttribute("title")).toContain("desarrollo");
    expect(byText("button", /Eliminar/)?.hasAttribute("disabled")).toBe(true);
  });

  it("shows the Airflow timeout instead of an empty list", async () => {
    state.hooks.useStudioDags = query(undefined, {
      isError: true,
      isSuccess: false,
      error: toApiError("Airflow DAG list timed out", 504),
    });
    await render();
    await openDagsTab();
    const notice = container.querySelector('[data-testid="dags-error"]');
    expect(notice?.textContent).toContain("HTTP 504");
  });

  it("asks for confirmation before deleting a DAG", async () => {
    const remove = state.hooks.useDeleteDag as Mutation;
    await render();
    await openDagsTab();
    await click(container.querySelector('[data-dag-id="acme_custom"]'));
    await click(byText("button", /Eliminar/));
    expect(remove.mutate).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="delete-dag-dialog"]')).toBeTruthy();
    await click(byText('[data-testid="delete-dag-dialog"] button', "Eliminar"));
    expect(remove.mutate.mock.calls[0][0]).toEqual({ cartridge: "acme", dagId: "acme_custom" });
  });

  it("links to the related tools instead of duplicating them", async () => {
    await render();
    const hrefs = [...container.querySelectorAll('nav[aria-label="Herramientas relacionadas"] a')].map((a) =>
      a.getAttribute("href"),
    );
    expect(hrefs).toEqual(["/data/inventory", "/copilot/knowledge", "/analytics"]);
  });
});
