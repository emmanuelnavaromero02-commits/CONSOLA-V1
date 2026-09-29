// @vitest-environment jsdom

import { readFileSync, readdirSync } from "node:fs";
import { join, sep } from "node:path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentsConsole } from "@/components/agents/AgentsConsole";
import { FrequencyPicker } from "@/components/schedule/FrequencyPicker";
import { EntitiesPanel } from "@/components/studio/EntitiesPanel";
import type { AgentRecord } from "@/lib/admin-surfaces";
import { FORBIDDEN_UI_TERMS, forbiddenTermsIn } from "@/lib/glossary";
import { FREQUENCY_PRESETS } from "@/lib/schedule/frequency";

const agentsApi = vi.hoisted(() => ({
  listAgents: vi.fn(),
  listAgentToolCatalog: vi.fn(),
  listAgentRuns: vi.fn(),
  getAgentRun: vi.fn(),
  createAgent: vi.fn(),
  updateAgent: vi.fn(),
  updateAgentStatus: vi.fn(),
  deleteAgent: vi.fn(),
  invokeAgent: vi.fn(),
  getMeAccess: vi.fn(),
}));
const studio = vi.hoisted(() => ({ hooks: {} as Record<string, unknown> }));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));
vi.mock("@/lib/admin-surfaces", () => agentsApi);
vi.mock("@/lib/studio/hooks", () => {
  const pick = (name: string) => () => studio.hooks[name];
  return {
    useStudioEntities: pick("useStudioEntities"),
    useUpdateEntity: pick("mutation"),
    useRenameEntity: pick("mutation"),
    useCreateEntity: pick("mutation"),
    useUploadEntitySpec: pick("mutation"),
    useIntrospectSource: pick("mutation"),
  };
});
vi.mock("@/lib/monitor/hooks", () => ({
  useSourceSchema: () => ({ data: undefined, isLoading: false, isError: false }),
  useEntityRuns: () => ({ data: [], isLoading: false, isError: false }),
  useEntityRunLogs: () => ({ data: undefined, isLoading: false, isError: false }),
  useJob: () => ({ data: undefined, isLoading: false, isError: false }),
  useJobLogs: () => ({ data: [], isLoading: false, isError: false }),
  findEntityRun: () => undefined,
  isTerminalRunStatus: () => false,
}));

const AGENT: AgentRecord = {
  id: "a-1",
  name: "Monitor de talento",
  slug: "monitor_de_talento",
  cartridge_id: "sap_successfactors",
  description: "Revisa indicadores agregados.",
  instructions: "Usa cifras agregadas.",
  personality: "",
  model: "claude-sonnet-4-6",
  max_tokens: 8192,
  temperature: 0.4,
  rag_filter: { kinds: ["policy"] },
  extra: { role: "monitor", category: "cartridge", scope: "cartridge", schedule: { cron: "*/30 * * * *", tz: "UTC", enabled: true } },
  is_active: true,
  allowed_tools: ["refinement__query_dataset", "mcp-infra__cartridge_get_manifest"],
};

const MIGRATED_SOURCES = [
  "../components/agents/AgentsConsole.tsx",
  "../components/schedule/FrequencyPicker.tsx",
  "../lib/schedule/frequency.ts",
  "../lib/agents/presets.ts",
  "../lib/agents/slug.ts",
  "../lib/agents/templates.ts",
  "../lib/agents/tool-labels.ts",
];

const SOURCE_ROOT = join(process.cwd(), "src");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    if (!/\.tsx?$/.test(entry.name) || entry.name.includes(".test.")) return [];
    return [path];
  });
}

const RENAMED_LABELS: Array<[string, string]> = [
  ["components/AppSidebar.tsx", "Fuentes de datos"],
  ["components/studio/DagsPanel.tsx", "Nueva automatización"],
  ["components/studio/DagsPanel.tsx", "Publicar automatización"],
  ["components/operations/VaultConnectionsTable.tsx", "Campos adicionales"],
  ["app/(shell)/dashboard/page.tsx", "Fuentes de datos conectadas"],
];

let container: HTMLDivElement;
let root: Root;

async function render(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
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

async function click(element: Element | null | undefined) {
  expect(element, "element to click").toBeTruthy();
  await act(async () => {
    element?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await settle();
}

function button(text: string | RegExp): HTMLElement | undefined {
  return [...container.querySelectorAll<HTMLElement>("button")].find((element) => {
    const content = element.textContent?.trim() ?? "";
    return typeof text === "string" ? content === text : text.test(content);
  });
}

function expectBusinessCopy(scope: Element | null | undefined, surface: string) {
  expect(scope, surface).toBeTruthy();
  expect(forbiddenTermsIn(scope?.textContent ?? ""), surface).toEqual([]);
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  agentsApi.listAgents.mockResolvedValue([AGENT]);
  agentsApi.listAgentToolCatalog.mockResolvedValue({
    refinement: [{ name: "query_dataset", risk_level: "read" }, { name: "get_data_catalog", risk_level: "read" }],
    "mcp-infra": [{ name: "cartridge_get_manifest", risk_level: "read" }, { name: "request_admin_help" }],
  });
  agentsApi.listAgentRuns.mockResolvedValue([]);
  agentsApi.getMeAccess.mockResolvedValue({ permissions: ["agents.read", "agents.execute"] });
  studio.hooks = {
    useStudioEntities: {
      data: { cartridge: "acme", total: 1, entities: [{ name: "Invoice", display_name: "Facturas", mode: "full", dag_id: "acme_invoice" }] },
      isLoading: false,
      isError: false,
      isFetching: false,
      refetch: vi.fn(),
    },
    mutation: { mutate: vi.fn(), isPending: false },
  };
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("business copy guard", () => {
  it("keeps technical vocabulary out of the agent editor default views", async () => {
    await render(<AgentsConsole />);
    expectBusinessCopy(container, "guardians catalog");
    await click(button("Administración técnica"));
    expectBusinessCopy(container, "agents list");
    await click(button(/Monitor de talento/));
    for (const tab of ["Configuración", "Herramientas", "Conocimiento", "Tareas", "Ejecuciones", "Probar"]) {
      await click(button(tab));
      expectBusinessCopy(container, `agents ${tab}`);
    }
    await click(button(/^Nuevo$/));
    expectBusinessCopy(container, "new agent");
  });

  it("keeps technical vocabulary out of the entity edit form", async () => {
    const manifest = { id: "acme", entities: [{ entity: "Invoice", trigger_type: "scheduled", cron_expression: "0 8 * * *", cron_timezone: "UTC" }] };
    await render(<EntitiesPanel cartridge="acme" manifest={manifest} />);
    await click(button(/^Editar/));
    expectBusinessCopy(container.querySelector('form[aria-label^="Editar entidad"]'), "entity edit form");
  });

  it("keeps technical vocabulary out of every preset view of the frequency picker", async () => {
    for (const preset of FREQUENCY_PRESETS) {
      await render(<FrequencyPicker value={{ cron: preset.cron ?? "", timeZone: "America/Mexico_City" }} onChange={() => undefined} />);
      expectBusinessCopy(container, `frequency ${preset.id}`);
    }
  });

  it("does not reintroduce the old data-source word in migrated sources", () => {
    for (const path of MIGRATED_SOURCES) {
      const source = readFileSync(new URL(path, import.meta.url), "utf8");
      expect(source, path).not.toMatch(/cartucho/i);
      for (const word of ["Slug", "Max tokens", "Temperatura", "Cartucho"]) {
        expect(source.includes(`>${word}<`) || source.includes(`"${word}"`), `${path}: ${word}`).toBe(false);
      }
    }
    expect(FORBIDDEN_UI_TERMS.length).toBeGreaterThan(10);
  });

  it("keeps the whole console free of the retired data-source word", () => {
    const offenders = sourceFiles(SOURCE_ROOT)
      .filter((path) => !path.endsWith(`lib${sep}glossary.ts`))
      .filter((path) => /cartucho/i.test(readFileSync(path, "utf8")));
    expect(offenders).toEqual([]);
  });

  it("keeps the renamed business labels in place", () => {
    for (const [path, label] of RENAMED_LABELS) {
      const source = readFileSync(join(SOURCE_ROOT, path), "utf8");
      expect(source, `${path}: ${label}`).toContain(label);
    }
  });
});
