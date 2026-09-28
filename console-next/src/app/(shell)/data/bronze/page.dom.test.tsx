// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import BronzeQueryPage from "./page";

const mocks = vi.hoisted(() => ({
  sources: { data: [] as string[], isLoading: false, isError: false, error: null as unknown },
  describeSource: vi.fn(),
  exploreData: vi.fn(),
  queryBronze: vi.fn(),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock("@/lib/monitor/hooks", () => ({ useSources: () => mocks.sources }));
vi.mock("@/lib/data/client", () => ({ queryBronze: mocks.queryBronze }));
vi.mock("@/lib/explorer/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/explorer/client")>();
  return { ...actual, describeSource: mocks.describeSource, exploreData: mocks.exploreData };
});

const SCHEMA = {
  source: "raw/acme/Employee",
  source_kind: "bronze",
  available_columns: [
    { name: "nombre", type: "VARCHAR", kind: "text" },
    { name: "salario", type: "DOUBLE", kind: "number" },
    { name: "load_date", type: "DATE", kind: "temporal" },
  ],
  executed: false,
  columns: [],
  rows: [],
  row_count: 0,
  limit: null,
  truncated: false,
  sql_display: "",
  sql_definition: "",
  sources: ["raw/acme/Employee"],
};

let container: HTMLDivElement;
let root: Root;

async function flush() {
  for (let tick = 0; tick < 5; tick += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <BronzeQueryPage />
      </QueryClientProvider>,
    );
  });
  await flush();
}

function sourceSelect(): HTMLSelectElement | null {
  return [...container.querySelectorAll<HTMLLabelElement>("label")]
    .find((label) => label.textContent?.startsWith("Fuente de datos"))
    ?.querySelector("select") ?? null;
}

function button(text: string): HTMLButtonElement | undefined {
  return [...container.querySelectorAll<HTMLButtonElement>("button")].find((item) => item.textContent?.includes(text));
}

async function choose(element: HTMLSelectElement | null, value: string) {
  expect(element).toBeTruthy();
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set?.call(element, value);
    element?.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
}

async function typeInto(element: HTMLInputElement | HTMLTextAreaElement | null, value: string) {
  expect(element).toBeTruthy();
  await act(async () => {
    const proto = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(element, value);
    element?.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function click(element: Element | null | undefined) {
  expect(element).toBeTruthy();
  await act(async () => {
    element?.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await flush();
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.sources = {
    data: ["raw/acme/Employee", "gold/ventas", "raw/acme/bad-name", "silver/acme/x"],
    isLoading: false,
    isError: false,
    error: null,
  };
  mocks.describeSource.mockResolvedValue(SCHEMA);
  mocks.exploreData.mockResolvedValue({
    ...SCHEMA,
    executed: true,
    columns: ["nombre", "salario"],
    rows: [["Ana", 120], ["Luis", 90]],
    row_count: 2,
    limit: 2,
    truncated: true,
    sql_display: "SELECT * FROM read_parquet('raw/acme/Employee') WHERE \"salario\" > 100 LIMIT 2",
    sql_definition: null,
  });
  mocks.queryBronze.mockResolvedValue({ data: [{ uno: 1 }], schema: [{ name: "uno" }] });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("Bronze query page", () => {
  it("hides raw SQL by default and asks for a data source first", async () => {
    await renderPage();
    expect(container.querySelector("h1")?.textContent).toBe("Consulta Bronze");
    expect(container.textContent).toContain("Elige una fuente de datos para construir la consulta sin escribir SQL.");
    expect(container.querySelector("details")?.open).toBe(false);
    const options = [...(sourceSelect()?.options ?? [])].map((option) => option.value);
    expect(options).toEqual(["", "raw/acme/Employee", "gold/ventas"]);
    expect(container.textContent).not.toContain("Sources");
  });

  it("runs the assisted builder through /api/data/explore", async () => {
    await renderPage();
    await choose(sourceSelect(), "raw/acme/Employee");
    expect(mocks.describeSource).toHaveBeenCalledWith({ kind: "bronze", cartridge: "acme", entity: "Employee" });
    await typeInto(container.querySelector<HTMLInputElement>('[data-testid="nl-bar"] input'), "salario mayor que 100");
    await act(async () => {
      container.querySelector('[data-testid="nl-bar"]')?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await click(button("Consultar"));
    expect(mocks.exploreData).toHaveBeenCalledWith({
      source: { kind: "bronze", cartridge: "acme", entity: "Employee" },
      columns: [],
      filters: [{ column: "salario", op: "gt", value: "100" }],
      sort: [],
      limit: 50,
      latest_only: false,
      execute: true,
    });
    expect(container.textContent).toContain("Se muestran las primeras 2 filas");
    expect(container.textContent).toContain("Ana");
    expect(container.querySelector('[data-testid="generated-sql"]')?.textContent).toContain("read_parquet('raw/acme/Employee')");
    expect(mocks.queryBronze).not.toHaveBeenCalled();
  });

  it("keeps the technical SQL path behind the disclosure", async () => {
    await renderPage();
    await choose(sourceSelect(), "raw/acme/Employee");
    await click(container.querySelector("summary"));
    expect(container.querySelector("details")?.open).toBe(true);
    const textarea = container.querySelector<HTMLTextAreaElement>('textarea[name="sql"]');
    expect(textarea?.labels?.[0]?.textContent).toBe("SQL");
    await typeInto(textarea, "select 1 as uno");
    await click(button("Ejecutar SQL"));
    expect(mocks.queryBronze).toHaveBeenCalledWith({ sql: "select 1 as uno", limit: 50, sources: ["raw/acme/Employee"] });
    expect(container.textContent).toContain("uno");
  });

  it("uses the dataset cap and no latest-load switch for gold sources", async () => {
    mocks.describeSource.mockResolvedValue({ ...SCHEMA, source: "gold/ventas", source_kind: "dataset" });
    await renderPage();
    await choose(sourceSelect(), "gold/ventas");
    expect(mocks.describeSource).toHaveBeenCalledWith({ kind: "dataset", name: "ventas" });
    expect(container.textContent).toContain("Máximo 10,000");
    expect(container.textContent).not.toContain("Solo la carga más reciente");
  });

  it("reports schema errors without inventing columns", async () => {
    mocks.describeSource.mockRejectedValue(new Error("Esta fuente de datos todavía no tiene archivos cargados."));
    await renderPage();
    await choose(sourceSelect(), "raw/acme/Employee");
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("todavía no tiene archivos cargados");
    expect(button("Consultar")?.disabled).toBe(true);
  });
});
