// @vitest-environment jsdom

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
  useSourceSchema: () => state.hooks.useSourceSchema,
  useEntityRuns: () => state.hooks.useEntityRuns,
  useEntityRunLogs: () => state.hooks.useEntityRunLogs,
  useJob: () => state.hooks.useJob,
  useJobLogs: () => state.hooks.useJobLogs,
  findEntityRun: () => undefined,
  isTerminalRunStatus: () => false,
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
    root.render(<StudioPage />);
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
  await click(byText('[role="tab"]', "DAGs"));
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

    await click(container.querySelector('[data-node-id="entity:Invoice"]'));
    const detail = container.querySelector('[data-testid="dag-graph-detail"]');
    expect(detail?.textContent).toContain("Invoice");
    expect(detail?.textContent).toContain("Entradas (1)");
    expect(detail?.textContent).toContain("Salidas (1)");
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
