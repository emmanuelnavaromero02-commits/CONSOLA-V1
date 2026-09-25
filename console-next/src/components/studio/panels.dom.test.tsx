// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { toApiError } from "@/lib/api";

import { EntitiesPanel } from "./EntitiesPanel";
import { LayersPanel } from "./LayersPanel";
import { RefinePanel } from "./RefinePanel";
import { StudioAssistant } from "./StudioAssistant";

type Mutation = { mutate: ReturnType<typeof vi.fn>; isPending: boolean; data?: unknown };

function query<T>(data: T, extra: Record<string, unknown> = {}) {
  return { data, isLoading: false, isError: false, isSuccess: true, isFetching: false, error: null, refetch: vi.fn(), ...extra };
}

function mutation(extra: Partial<Mutation> = {}): Mutation {
  return { mutate: vi.fn(), isPending: false, ...extra };
}

const state = vi.hoisted(() => ({ hooks: {} as Record<string, unknown> }));
const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
const clientMocks = vi.hoisted(() => ({ extractEntity: vi.fn(), queryBronze: vi.fn(), streamStudioChat: vi.fn() }));

vi.mock("sonner", () => ({ toast: toastMock }));

vi.mock("@/lib/studio/hooks", () => {
  const pick = (name: string) => () => state.hooks[name];
  return {
    useStudioEntities: pick("useStudioEntities"),
    useUpdateEntity: pick("useUpdateEntity"),
    useRenameEntity: pick("useRenameEntity"),
    useCreateEntity: pick("useCreateEntity"),
    useUploadEntitySpec: pick("useUploadEntitySpec"),
    useIntrospectSource: pick("useIntrospectSource"),
    useLayerPreview: pick("useLayerPreview"),
    usePublishSuperset: pick("usePublishSuperset"),
    useRuntimeConfig: pick("useRuntimeConfig"),
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
  useJob: () => query(undefined),
  useJobLogs: () => query([]),
  findEntityRun: (runs: Array<{ dag_run_id?: string }> | undefined, id: string) =>
    runs?.find((run) => run.dag_run_id === id),
  isTerminalRunStatus: (status?: string) => status === "success",
}));

vi.mock("@/lib/monitor/client", () => ({ extractEntity: clientMocks.extractEntity }));
vi.mock("@/lib/data/client", () => ({ queryBronze: clientMocks.queryBronze }));
vi.mock("@/lib/studio/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/studio/client")>();
  return { ...actual, streamStudioChat: clientMocks.streamStudioChat };
});

let container: HTMLDivElement;
let root: Root;

async function render(node: React.ReactNode) {
  await act(async () => {
    root.render(node);
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

async function typeInto(element: HTMLInputElement | HTMLTextAreaElement | null, value: string) {
  expect(element).toBeTruthy();
  await act(async () => {
    const proto = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(element, value);
    element?.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  Element.prototype.scrollIntoView = vi.fn();
  state.hooks = {
    useStudioEntities: query({
      cartridge: "acme",
      total: 1,
      entities: [{ name: "Invoice", display_name: "Facturas", mode: "incremental", dag_id: "acme_invoice", source: "entity_config" }],
    }),
    useUpdateEntity: mutation(),
    useRenameEntity: mutation(),
    useCreateEntity: mutation(),
    useUploadEntitySpec: mutation(),
    useIntrospectSource: mutation(),
    useLayerPreview: query({ layer: "silver", columns: [], rows: [], total: 0, available: false, reason: "No silver datasets registered for cartridge acme" }),
    usePublishSuperset: mutation(),
    useRuntimeConfig: query({ airflow_url: null, superset_url: null }),
    useSaveDataset: mutation(),
    useRefreshDataset: mutation(),
    useDeleteDataset: mutation(),
    useDatasets: query([
      { name: "orders", layer: "silver", cartridge: "acme" },
      { name: "gold_sales", layer: "gold", cartridge: "acme" },
      { name: "foreign", layer: "silver", cartridge: "beta" },
    ]),
    useDatasetDetail: query({ name: "orders", layer: "silver", sql: "select 1", sources: ["raw/acme/Invoice"], metadata: { description: "Pedidos" } }),
    useSourceSchema: query({ status: "ok", preview: { schema: [{ name: "id", type: "VARCHAR" }], data: [{ id: "1" }] } }),
    useEntityRuns: query([{ dag_run_id: "run-1", status: "running" }]),
    useEntityRunLogs: query({ logs: [{ task_id: "extract", available: true, logs: "linea de log" }] }),
  };
  vi.clearAllMocks();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("LayersPanel", () => {
  it("lists only this cartridge's datasets per layer and shows unavailable previews honestly", async () => {
    await render(<LayersPanel cartridge="acme" />);
    expect(container.textContent).toContain("orders");
    expect(container.textContent).not.toContain("foreign");
    expect(container.querySelector('[data-testid="layer-unavailable"]')?.textContent).toContain(
      "No silver datasets registered for cartridge acme",
    );
  });

  it("offers Superset publishing only when /api/config reports it and explains the internal-only case", async () => {
    await render(<LayersPanel cartridge="acme" />);
    await click(byText('[role="tab"]', "Gold"));
    expect(byText("button", /Publicar en Superset/)).toBeUndefined();
    expect(container.textContent).toContain("Superset está disponible solo internamente por seguridad");

    await act(async () => root.unmount());
    root = createRoot(container);
    state.hooks.useRuntimeConfig = query({ superset_url: "https://bi.example.test" });
    const publish = state.hooks.usePublishSuperset as Mutation;
    publish.mutate.mockImplementation((_input: unknown, options: { onError?: (error: unknown) => void }) =>
      options.onError?.(toApiError("x", 503)),
    );
    await render(<LayersPanel cartridge="acme" />);
    await click(byText('[role="tab"]', "Gold"));
    await click(byText("button", /Publicar en Superset/));
    expect(publish.mutate.mock.calls[0][0]).toEqual({ cartridge: "acme", tableName: "gold_sales" });
    expect(toastMock.error).toHaveBeenCalledWith(expect.stringContaining("solo internamente"));
  });
});

describe("EntitiesPanel", () => {
  it("shows the static fallback of the introspection honestly", async () => {
    state.hooks.useIntrospectSource = mutation({
      data: { source: "static_connector_schema", reason: "no credentials available", entities: [{ entity: "Invoice", fields: [] }] },
    });
    await render(<EntitiesPanel cartridge="acme" />);
    const result = container.querySelector('[data-testid="introspection-result"]');
    expect(result?.textContent).toContain("Esquema estático (fallback)");
    expect(result?.textContent).toContain("no credentials available");
  });

  it("extracts now and follows the DAG run with its logs", async () => {
    clientMocks.extractEntity.mockResolvedValue({ dag_id: "acme_invoice", dag_run_id: "run-1", job_id: "run-1" });
    await render(<EntitiesPanel cartridge="acme" />);
    await click(byText("button", /Extraer ahora/));
    expect(clientMocks.extractEntity).toHaveBeenCalledWith("acme", "Invoice", { mode: "incremental" });
    const tracker = container.querySelector('[data-testid="extraction-tracker"]');
    expect(tracker?.textContent).toContain("run-1");
    expect(tracker?.textContent).toContain("En ejecución");
    await click(byText("button", /Ver logs/));
    expect(container.textContent).toContain("linea de log");
  });

  it("patches only the edited fields", async () => {
    const update = state.hooks.useUpdateEntity as Mutation;
    await render(<EntitiesPanel cartridge="acme" />);
    await click(byText("button", /^Editar/));
    await typeInto(container.querySelector<HTMLInputElement>('input[name="cron_expression"]'), "0 8 * * *");
    await click(byText("button", /Guardar cambios/));
    expect(update.mutate.mock.calls[0][0]).toEqual({
      cartridge: "acme",
      entity: "Invoice",
      patch: { cron_expression: "0 8 * * *", trigger_type: "scheduled" },
    });
  });

  it("renders the bronze schema for an entity", async () => {
    await render(<EntitiesPanel cartridge="acme" />);
    await click(byText("button", /Esquema/));
    expect(container.textContent).toContain("id");
    expect(container.textContent).toContain("VARCHAR");
  });
});

describe("RefinePanel", () => {
  it("previews with the declared sources and confirms before deleting", async () => {
    clientMocks.queryBronze.mockResolvedValue({ data: [{ total: 3 }], schema: [{ name: "total" }] });
    const remove = state.hooks.useDeleteDataset as Mutation;
    await render(
      <QueryClientProvider client={new QueryClient()}>
        <RefinePanel cartridge="acme" />
      </QueryClientProvider>,
    );
    await click(byText("button", /^orders/));
    expect(container.querySelector<HTMLTextAreaElement>('textarea[name="sql"]')?.value).toBe("select 1");
    await click(byText("button", /Previsualizar/));
    for (let tick = 0; tick < 5; tick += 1) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    }
    expect(clientMocks.queryBronze).toHaveBeenCalledWith({ sql: "select 1", limit: 50, sources: ["raw/acme/Invoice"] });
    expect(container.textContent).toContain("total");

    await click(byText("button", /^Eliminar/));
    expect(remove.mutate).not.toHaveBeenCalled();
    await click(byText('[data-testid="delete-dataset-dialog"] button', "Eliminar"));
    expect(remove.mutate.mock.calls[0][0]).toBe("orders");
  });
});

describe("StudioAssistant", () => {
  it("streams through the Studio endpoint client and shows the reply", async () => {
    clientMocks.streamStudioChat.mockImplementation(
      async (_input: unknown, handlers: { onToolUse?: (tool: string) => void }) => {
        handlers.onToolUse?.("cartridge_get_manifest");
        return { reply: "Listo", history: [{ role: "assistant" }], viewerUrls: [] };
      },
    );
    await render(<StudioAssistant cartridge="acme" step={2} onClose={() => undefined} />);
    const input = container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Mensaje para el asistente de Studio"]');
    await typeInto(input, "Revisa los DAGs");
    await click(byText("button", "Enviar"));
    expect(clientMocks.streamStudioChat.mock.calls[0][0]).toEqual({
      message: "Revisa los DAGs",
      history: [],
      step: 2,
      cartridge_id: "acme",
    });
    expect(container.textContent).toContain("Listo");
    expect(container.textContent).toContain("cartridge_get_manifest");
  });
});
