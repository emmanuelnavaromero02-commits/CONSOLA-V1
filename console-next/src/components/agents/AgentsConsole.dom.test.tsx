// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentRecord, AgentToolCatalogItem } from "@/lib/admin-surfaces";
import { forbiddenTermsIn } from "@/lib/glossary";

import { AgentsConsole } from "./AgentsConsole";

const api = vi.hoisted(() => ({
  listAgents: vi.fn(),
  listAgentToolCatalog: vi.fn(),
  listAgentRuns: vi.fn(),
  getAgentRun: vi.fn(),
  createAgent: vi.fn(),
  updateAgent: vi.fn(),
  updateAgentStatus: vi.fn(),
  deleteAgent: vi.fn(),
  invokeAgent: vi.fn(),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock("@/lib/admin-surfaces", () => api);

const CATALOG: Record<string, AgentToolCatalogItem[]> = {
  refinement: [
    { name: "get_data_catalog", description: "Catalog", risk_level: "read" },
    { name: "list_datasets", risk_level: "read" },
    { name: "get_schema", risk_level: "read" },
    { name: "describe_silver", risk_level: "read" },
    { name: "query_dataset", risk_level: "read" },
    { name: "get_lineage", risk_level: "read" },
  ],
  "mcp-infra": [
    { name: "search_rag", risk_level: "read" },
    { name: "request_admin_help", risk_level: "write", requires_approval: true },
  ],
  infra: [{ name: "search_rag", risk_level: "read" }],
};

const EXISTING: AgentRecord = {
  id: "a-1",
  name: "Monitor de talento",
  slug: "monitor_de_talento",
  cartridge_id: "sap_successfactors",
  description: "Revisa indicadores de talento.",
  instructions: "Revisa los indicadores agregados.",
  personality: "",
  model: "claude-opus-custom",
  max_tokens: 4000,
  temperature: 0.4,
  rag_filter: { kinds: ["policy", "metric", "runbook"] },
  extra: { role: "monitor", monitor: { wisdom_bit_id: "wb-1" }, schedule: { cron: "0 8 * * *", tz: "UTC", enabled: true } },
  is_active: true,
  allowed_tools: ["refinement__query_dataset", "mcp-infra__search_rag"],
};

let container: HTMLDivElement;
let root: Root;

async function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <AgentsConsole />
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
  await settle();
}

async function typeInto(element: HTMLInputElement | HTMLTextAreaElement | null | undefined, value: string) {
  expect(element).toBeTruthy();
  await act(async () => {
    const proto = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(element, value);
    element?.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function fieldInput(label: string): HTMLInputElement | HTMLTextAreaElement | null {
  const wrapper = [...container.querySelectorAll("label")].find(
    (item) => item.querySelector(":scope > span")?.textContent === label,
  );
  return wrapper?.querySelector("input, textarea") ?? null;
}

async function saveButton() {
  await click(byText("button", "Guardar"));
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const original = Intl.DateTimeFormat.prototype.resolvedOptions;
  vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockImplementation(function (this: Intl.DateTimeFormat) {
    return { ...original.call(this), timeZone: "America/Mexico_City" };
  });
  vi.clearAllMocks();
  api.listAgents.mockResolvedValue([EXISTING, { ...EXISTING, id: "a-2", slug: "oficial_de_cumplimiento_normativo", name: "Otro", extra: {} }]);
  api.listAgentToolCatalog.mockResolvedValue(CATALOG);
  api.listAgentRuns.mockResolvedValue([]);
  api.createAgent.mockImplementation(async (payload) => ({ id: "new-1", ...payload }));
  api.updateAgent.mockImplementation(async (id, payload) => ({ id, ...payload }));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe("AgentsConsole executive profiles", () => {
  it("fills a profile with the real subset of its tools and warns about the rest", async () => {
    await render();
    await click(byText("button", /^Nuevo$/));
    await click(container.querySelector('[data-agent-template="talent_mobility"]'));
    expect((fieldInput("Nombre") as HTMLInputElement).value).toBe("Analista de Movilidad y Retención de Talento");
    expect(container.textContent).toContain("herramientas que no están disponibles en este entorno: Indicadores de talento");
    await saveButton();
    const payload = api.createAgent.mock.calls[0][0];
    expect(payload.slug).toBe("analista_de_movilidad_y_retencion_de_talento");
    expect(payload.temperature).toBe(0.8);
    expect(payload.allowed_tools).toEqual([
      "mcp-infra__request_admin_help",
      "mcp-infra__search_rag",
      "refinement__describe_silver",
      "refinement__get_data_catalog",
      "refinement__get_schema",
      "refinement__list_datasets",
      "refinement__query_dataset",
    ]);
    expect(payload.rag_filter).toEqual({ kinds: ["document", "schema"] });
    expect(payload.model).toBe("claude-sonnet-4-6");
    expect(payload.max_tokens).toBe(8192);
    expect(payload.extra.schedule).toBeUndefined();
  });

  it("derives a unique slug from the name and never exposes it as a field", async () => {
    await render();
    await click(byText("button", /^Nuevo$/));
    expect(fieldInput("Slug")).toBeNull();
    await click(container.querySelector('[data-agent-template="compliance_officer"]'));
    await saveButton();
    expect(api.createAgent.mock.calls[0][0].slug).toBe("oficial_de_cumplimiento_normativo_2");
    expect(api.createAgent.mock.calls[0][0].temperature).toBe(0.1);
  });

  it("keeps the slug, model and token budget of an existing agent on update", async () => {
    await render();
    await click(byText("button", /Monitor de talento/));
    await typeInto(fieldInput("Nombre"), "Monitor de talento regional");
    await saveButton();
    const [id, payload] = api.updateAgent.mock.calls[0];
    expect(id).toBe("a-1");
    expect(payload.slug).toBe("monitor_de_talento");
    expect(payload.name).toBe("Monitor de talento regional");
    expect(payload.model).toBe("claude-opus-custom");
    expect(payload.max_tokens).toBe(4000);
    expect(payload.temperature).toBe(0.4);
  });

  it("toggles response style between precision and creative, preserving the current value until chosen", async () => {
    await render();
    await click(byText("button", /Monitor de talento/));
    const current = container.querySelector<HTMLInputElement>('input[name="agent_response_style"][value="current"]');
    expect(current?.checked).toBe(true);
    expect(container.textContent).toContain("Equilibrado (valor actual)");
    await click(byText("label", /Creativo y Exploratorio/));
    expect(container.querySelector('input[name="agent_response_style"][value="current"]')).toBeNull();
    await saveButton();
    expect(api.updateAgent.mock.calls[0][1].temperature).toBe(0.8);
    await click(byText("label", /Máxima Precisión/));
    await saveButton();
    expect(api.updateAgent.mock.calls[1][1].temperature).toBe(0.1);
  });

  it("maps legacy knowledge kinds to the ones the search understands", async () => {
    await render();
    await click(byText("button", /Monitor de talento/));
    await click(byText("button", "Conocimiento"));
    expect(container.textContent).toContain("Se ajustaron tipos de conocimiento anteriores");
    const boxes = [...container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')];
    expect(boxes.map((box) => box.checked)).toEqual([true, true]);
    expect(container.textContent).toContain("Documentos y políticas");
    expect(container.textContent).toContain("Estructura de datos");
    expect(container.textContent).toContain("Datos de contexto");
    await saveButton();
    expect(api.updateAgent.mock.calls[0][1].rag_filter).toEqual({ kinds: ["document", "schema"] });
  });

  it("schedules tasks with business frequencies and a time zone", async () => {
    await render();
    await click(byText("button", /Monitor de talento/));
    await click(byText("button", "Tareas"));
    expect(container.querySelector<HTMLInputElement>('input[name="agent_frequency"][value="daily_morning"]')?.checked).toBe(true);
    await click(byText("label", /Al finalizar la jornada laboral/));
    const zone = container.querySelector<HTMLSelectElement>('select[name="agent_frequency_timezone"]');
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set?.call(zone, "America/Mexico_City");
      zone?.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await saveButton();
    expect(api.updateAgent.mock.calls[0][1].extra.schedule).toMatchObject({
      cron: "0 19 * * 1-5",
      tz: "America/Mexico_City",
      enabled: true,
    });
    await click(byText("label", /Bajo Demanda/));
    await saveButton();
    expect(api.updateAgent.mock.calls[1][1].extra.schedule).toMatchObject({ cron: "", enabled: false });
  });

  it("shows tools with Spanish names, hides the duplicate alias server and keeps raw ids in tooltips", async () => {
    await render();
    await click(byText("button", /Monitor de talento/));
    await click(byText("button", "Herramientas"));
    expect(container.textContent).toContain("2 herramientas asignadas");
    expect(container.textContent).toContain("Consultar un conjunto de datos");
    expect(container.textContent).toContain("Escalar al administrador");
    expect(container.textContent).toContain("Requiere aprobación");
    expect(container.textContent).not.toContain("refinement__query_dataset");
    expect(container.querySelector('[title^="refinement__query_dataset"]')).toBeTruthy();
    expect(container.querySelectorAll("section h3")).toHaveLength(2);
  });

  it("keeps technical vocabulary out of every default tab", async () => {
    await render();
    await click(byText("button", /Monitor de talento/));
    for (const tab of ["Configuración", "Herramientas", "Conocimiento", "Tareas"]) {
      await click(byText("button", tab));
      expect(forbiddenTermsIn(container.textContent ?? ""), tab).toEqual([]);
    }
    await click(byText("button", /^Nuevo$/));
    expect(forbiddenTermsIn(container.textContent ?? "")).toEqual([]);
  });
});
