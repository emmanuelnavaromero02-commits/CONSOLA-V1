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
    expect(byText('[role="tab"]', "Gold")?.getAttribute("aria-selected")).toBe("true");
    expect(container.textContent).toContain("gold_sales");
    await click(byText('[role="tab"]', "Silver"));
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

describe("LayersPanel preview table", () => {
  it("uses the dataset's real row count as the total and pages the loaded rows", async () => {
    state.hooks.useDatasets = query([{ name: "gold_sales", layer: "gold", cartridge: "acme", row_count: 1240 }]);
    state.hooks.useLayerPreview = query({
      layer: "gold",
      dataset: "gold_sales",
      columns: ["id", "total"],
      rows: Array.from({ length: 30 }, (_, index) => ({ id: index + 1, total: index * 10 })),
      total: 30,
      available: true,
    });
    await render(<LayersPanel cartridge="acme" />);
    expect(container.querySelector('[data-testid="table-summary"]')?.textContent).toBe(
      "Mostrando 1–25 de 1,240 registros · 30 cargados en la vista previa",
    );
    expect(container.querySelectorAll("tbody tr")).toHaveLength(25);
    expect(container.textContent).toContain("gold_sales · 2 columnas");
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
    expect(container.querySelector('label[for="studio-sql"]')?.textContent).toBe("SQL");
    expect(container.querySelector('textarea[name="sql"]')?.id).toBe("studio-sql");
    expect(container.querySelector('[data-testid="sql-gutter"]')?.children).toHaveLength(1);
    expect(container.textContent).toContain("Consola SQL · 1 línea");
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

describe("RefinePanel initial target", () => {
  it("opens an existing dataset without a click", async () => {
    await render(
      <QueryClientProvider client={new QueryClient()}>
        <RefinePanel cartridge="acme" initialTarget={{ dataset: "orders" }} />
      </QueryClientProvider>,
    );
    expect(container.querySelector<HTMLTextAreaElement>('textarea[name="sql"]')?.value).toBe("select 1");
    expect(byText("button", /^orders/)?.getAttribute("aria-pressed")).toBe("true");
  });

  it("starts a new draft on a bronze entity without inventing SQL", async () => {
    await render(
      <QueryClientProvider client={new QueryClient()}>
        <RefinePanel
          cartridge="acme"
          manifest={{ id: "acme", entities: [{ entity: "Invoice" }, { entity: "Customer" }] }}
          initialTarget={{ entity: "Customer" }}
        />
      </QueryClientProvider>,
    );
    const editor = container.querySelector('[data-testid="refine-editor"]');
    const entity = [...(editor?.querySelectorAll("select") ?? [])].find((select) =>
      [...select.options].some((option) => option.value === "Customer"),
    );
    expect(entity?.value).toBe("Customer");
    expect(container.querySelector<HTMLTextAreaElement>('textarea[name="sql"]')?.value).toBe("");
    expect(container.textContent).toContain("raw/acme/Customer");
  });
});

// Deterministic goal-run approval id, built from parts so it is not a literal token.
const APPROVAL_UUID = ["5d2c7a8e", "1b3f", "5c4d", "9e8f", "0a1b2c3d4e5f"].join("-");

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

  it("scrolls the conversation without smooth motion when reduced motion is requested", async () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("prefers-reduced-motion"),
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }));
    const scrollTo = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollTo", { value: scrollTo, configurable: true, writable: true });
    let finish: (value: unknown) => void = () => undefined;
    clientMocks.streamStudioChat.mockImplementation(() => new Promise((resolve) => {
      finish = resolve;
    }));
    try {
      await render(<StudioAssistant cartridge="acme" step={1} onClose={() => undefined} />);
      await typeInto(container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Mensaje para el asistente de Studio"]'), "Hola");
      await click(byText("button", "Enviar"));
      expect(scrollTo).toHaveBeenCalled();
      expect(scrollTo.mock.calls.every(([options]) => options.behavior === "auto")).toBe(true);
      const dot = container.querySelector('[data-testid="chat-messages"] span[aria-hidden]');
      expect(dot?.className).toContain("motion-safe:animate-pulse");
      expect(dot?.className).not.toMatch(/(^|\s)animate-pulse/);
      await act(async () => {
        finish({ reply: "Listo", history: [], viewerUrls: [] });
      });
    } finally {
      delete (HTMLElement.prototype as { scrollTo?: unknown }).scrollTo;
      vi.unstubAllGlobals();
    }
  });

  it("offers the section's suggested questions and sends them", async () => {
    clientMocks.streamStudioChat.mockResolvedValue({ reply: "Revisado", history: [], viewerUrls: [] });
    await render(<StudioAssistant cartridge="acme" step={2} onClose={() => undefined} />);
    expect(container.textContent).toContain("Sección: Automatizaciones");
    const prompts = [...container.querySelectorAll('[aria-label="Preguntas sugeridas"] button')];
    expect(prompts.map((button) => button.textContent)).toEqual([
      "¿Por qué falló la última extracción?",
      "¿Cómo cambio la frecuencia a diaria?",
    ]);
    await click(prompts[0]);
    expect(clientMocks.streamStudioChat.mock.calls[0][0]).toMatchObject({
      message: "¿Por qué falló la última extracción?",
      step: 2,
      cartridge_id: "acme",
    });
  });

  it("builds the suggestions from the cartridge's own tables", async () => {
    await render(
      <StudioAssistant
        cartridge="acme"
        step={3}
        manifest={{ id: "acme", entities: [{ entity: "TimeEntry", display_name: "Registro de horas" }] }}
        onClose={() => undefined}
      />,
    );
    const prompts = [...container.querySelectorAll('[aria-label="Preguntas sugeridas"] button')].map((button) => button.textContent);
    expect(prompts).toEqual([
      "¿Qué campos incluye la tabla Registro de horas?",
      "¿Hay registros duplicados en Registro de horas?",
    ]);
  });

  it("renders code blocks as collapsible cards with copy", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    clientMocks.streamStudioChat.mockResolvedValue({ reply: "Listo\n```sql\nselect 1\n```", history: [], viewerUrls: [] });
    await render(<StudioAssistant cartridge="acme" step={4} onClose={() => undefined} />);
    await typeInto(container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Mensaje para el asistente de Studio"]'), "SQL");
    await click(byText("button", "Enviar"));
    const card = container.querySelector('[data-testid="assistant-code-card"]');
    expect(card?.textContent).toContain("sql");
    expect(card?.querySelector("pre")).toBeNull();
    const toggle = byText('[data-testid="assistant-code-card"] button', "Ver código");
    expect(toggle?.getAttribute("aria-expanded")).toBe("false");
    await click(toggle);
    expect(toggle?.getAttribute("aria-expanded")).toBe("true");
    expect(card?.querySelector("pre code")?.textContent).toBe("select 1");
    await click(byText('[data-testid="assistant-code-card"] button', "Copiar"));
    expect(writeText).toHaveBeenCalledWith("select 1");
    expect(toastMock.success).toHaveBeenCalledWith("Código copiado.");
    expect(byText("button", "Aplicar cambio")).toBeUndefined();
  });

  it("offers Aplicar cambio only for a real goal-run approval and sends the exact message", async () => {
    const approval = {
      approval_required: true,
      approval_key: APPROVAL_UUID,
      step_id: 7,
      goal_run_id: "g",
      tool: "materialize",
      risk_level: "write",
      reason: "Materializar ventas",
    };
    clientMocks.streamStudioChat
      .mockResolvedValueOnce({
        reply: "Necesito tu aprobación.",
        history: [
          { role: "user", content: "materializa" },
          {
            role: "user",
            content: [{ type: "tool_result", tool_use_id: "t1", content: JSON.stringify({ approval_required: true, approval }) }],
          },
        ],
        viewerUrls: [],
      })
      .mockResolvedValueOnce({ reply: "Aprobado.", history: [], viewerUrls: [] });
    await render(<StudioAssistant cartridge="acme" step={4} onClose={() => undefined} />);
    await typeInto(container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Mensaje para el asistente de Studio"]'), "materializa");
    await click(byText("button", "Enviar"));
    const card = container.querySelector('[data-testid="assistant-approval-card"]');
    expect(card?.textContent).toContain("Cambio pendiente de aprobación");
    expect(card?.textContent).toContain("materialize");
    expect(card?.textContent).toContain("Paso 7");
    await click(byText("button", "Aplicar cambio"));
    expect(clientMocks.streamStudioChat).toHaveBeenCalledTimes(1);
    const dialog = container.querySelector('[data-testid="assistant-approval-dialog"]');
    expect(dialog?.textContent).toContain(`Apruebo el paso 7 (approval_key ${APPROVAL_UUID}).`);
    await click(byText('[data-testid="assistant-approval-dialog"] button', "Aprobar y enviar"));
    expect(clientMocks.streamStudioChat).toHaveBeenCalledTimes(2);
    expect(clientMocks.streamStudioChat.mock.calls[1][0].message).toBe(`Apruebo el paso 7 (approval_key ${APPROVAL_UUID}).`);
    expect(container.querySelector('[data-testid="assistant-approval-card"]')).toBeNull();
  });

  it("rejects a pending approval with the exact rejection message", async () => {
    clientMocks.streamStudioChat.mockResolvedValue({
      reply: "Pendiente",
      history: [{ role: "user", content: [{ type: "tool_result", content: JSON.stringify({ approval_required: true, approval_key: APPROVAL_UUID, step_id: 3 }) }] }],
      viewerUrls: [],
    });
    await render(<StudioAssistant cartridge="acme" step={4} onClose={() => undefined} />);
    await typeInto(container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Mensaje para el asistente de Studio"]'), "hazlo");
    await click(byText("button", "Enviar"));
    await click(byText("button", /Rechazar/));
    expect(clientMocks.streamStudioChat.mock.calls[1][0].message).toBe(`Rechazo el paso 3 (approval_key ${APPROVAL_UUID}).`);
  });

  it("shows no approval card when the history has no approval key", async () => {
    clientMocks.streamStudioChat.mockResolvedValue({
      reply: "Hecho",
      history: [{ role: "user", content: [{ type: "tool_result", content: JSON.stringify({ approval_required: true, step_id: 1 }) }] }],
      viewerUrls: [],
    });
    await render(<StudioAssistant cartridge="acme" step={4} onClose={() => undefined} />);
    await typeInto(container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Mensaje para el asistente de Studio"]'), "hazlo");
    await click(byText("button", "Enviar"));
    expect(container.textContent).toContain("Hecho");
    expect(container.querySelector('[data-testid="assistant-approval-card"]')).toBeNull();
    expect(byText("button", "Aplicar cambio")).toBeUndefined();
  });
});
