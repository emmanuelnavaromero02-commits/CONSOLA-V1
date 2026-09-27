// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DataCatalogPayload } from "@/lib/data/types";

const client = vi.hoisted(() => ({
  getDataCatalog: vi.fn(),
  autoProfileCatalog: vi.fn(),
  getDatasetRows: vi.fn(),
  registerCatalogRelationship: vi.fn(),
  rejectCatalogRelationship: vi.fn(),
  upsertCatalogEntry: vi.fn(),
}));
const access = vi.hoisted(() => ({ getMeAccess: vi.fn() }));

vi.mock("@/lib/data/client", () => client);
vi.mock("@/lib/admin-surfaces", () => access);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import DataCatalogPage from "./page";

const CATALOG: DataCatalogPayload = {
  datasets: {
    sap_successfactors_employees: {
      layer: "silver",
      cartridge: "sap_successfactors",
      display_name: "Empleados",
      description:
        "Empleados de SAP SuccessFactors: 10 000 registros, actualizados el 25 sep 2026. Contiene datos personales identificables (correo electrónico, RFC); trátela como confidencial.",
      description_origin: "copilot",
      kind: "dataset",
      row_count: 10000,
      last_refresh: "2026-09-25T10:00:00+00:00",
      copilot: { status: "ready", pii_columns: 2, financial_columns: 1, relations: 1 },
      columns: [
        { name: "employee_id", type: "VARCHAR", semantic_type: "identifier", is_key: true, description: "Identificador único de empleado." },
        {
          name: "email",
          type: "VARCHAR",
          semantic_type: "text",
          classifications: ["pii", "confidential"],
          classification_origin: "copilot",
          copilot_confidence: 0.97,
          copilot_basis: ["name:email", "pattern:email"],
          stats_redacted: true,
          description: "Correo electrónico de la persona: dato personal identificable; acceso restringido.",
        },
        { name: "salary_amount", type: "DECIMAL(18,2)", semantic_type: "money", classifications: ["financial", "confidential"] },
        { name: "headcount", type: "BIGINT", semantic_type: "integer" },
      ],
    },
    sap_successfactors_departments: {
      layer: "gold",
      cartridge: "sap_successfactors",
      display_name: "Departamentos",
      description: "Departamentos vigentes (carga parcial)",
      description_origin: "manual",
      row_count: 40,
      columns: [{ name: "department_id", type: "VARCHAR", is_key: true }],
    },
  },
  relationships: [
    {
      from_dataset: "sap_successfactors_employees",
      from_column: "department_id",
      to_dataset: "sap_successfactors_departments",
      to_column: "department_id",
      cardinality: "N:1",
      origin: "copilot",
      status: "active",
      confidence: 1,
    },
  ],
};

let container: HTMLDivElement;
let root: Root;

async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function renderPage(role: { global?: string; workspace_role?: string } = {}) {
  access.getMeAccess.mockResolvedValue({
    role: { global: role.global ?? "user" },
    workspace: { workspace_role: role.workspace_role ?? "viewer" },
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <DataCatalogPage />
      </QueryClientProvider>,
    );
  });
  await flush(0);
  await flush(0);
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/data/inventory");
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
  client.getDataCatalog.mockResolvedValue(CATALOG);
  client.autoProfileCatalog.mockResolvedValue({ status: "idle", processed: 0, pending: 0, stale: 0 });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("Data catalog page", () => {
  it("shows Copilot documentation, sensitivity and relations with zero clicks", async () => {
    await renderPage();
    const text = container.textContent ?? "";
    expect(text).toContain("✨ Autocatalogado por Copiloto");
    expect(text).toContain("Empleados de SAP SuccessFactors: 10 000 registros");
    expect(text).toContain("Dato Personal Identificable");
    expect(text).toContain("Información Financiera");
    expect(text).toContain("Identificador");
    expect(text).toContain("Monto Monetario");
    expect(container.querySelector("svg[role='img']")?.getAttribute("aria-label")).toBe(
      "Grafo de relaciones: 2 tablas y 1 relaciones",
    );
    expect(container.querySelector("table[aria-label='Matriz de relaciones entre tablas']")).toBeTruthy();
    expect(text).toContain("1 detectadas por Copiloto");
    expect(text).toContain("Columnas con datos personales");
  });

  it("keeps the readiness badge for partial loads", async () => {
    await renderPage();
    expect(container.textContent).toContain("Parcial");
  });

  it("never shows raw technical types in the main view", async () => {
    await renderPage();
    const text = container.textContent ?? "";
    expect(text).not.toContain("VARCHAR");
    expect(text).not.toContain("BIGINT");
    expect(text).not.toContain("DECIMAL");
  });

  it("asks for auto-profiling exactly once, 800 ms after opening", async () => {
    await renderPage();
    expect(client.autoProfileCatalog).not.toHaveBeenCalled();
    await flush(799);
    expect(client.autoProfileCatalog).not.toHaveBeenCalled();
    await flush(1);
    expect(client.autoProfileCatalog).toHaveBeenCalledTimes(1);
    expect(client.autoProfileCatalog).toHaveBeenCalledWith({ cartridge: undefined, include_sources: false });
    await flush(30_000);
    expect(client.autoProfileCatalog).toHaveBeenCalledTimes(1);
  });

  it("does not default to a data source and derives the options from the data", async () => {
    await renderPage();
    expect(client.getDataCatalog).toHaveBeenCalledWith({
      layer: "",
      cartridge: "",
      tags: "",
      datasets: "",
      include_sources: false,
    });
    const labels = [...container.querySelectorAll("label > span")].map((node) => node.textContent);
    expect(labels).toContain("Fuente de datos");
    expect(labels).toContain("Clasificación");
    const source = [...container.querySelectorAll("label")].find((node) =>
      node.querySelector("span")?.textContent === "Fuente de datos",
    );
    const options = [...(source?.querySelectorAll("option") ?? [])].map((option) => option.textContent);
    expect(options).toEqual(["Todas", "SAP SuccessFactors"]);
  });

  it("hides the DBA accordion for viewers and shows it collapsed for admins", async () => {
    await renderPage();
    expect(container.textContent).not.toContain("Ajustes Avanzados para DBAs");
    await act(async () => root.unmount());
    root = createRoot(container);
    await renderPage({ workspace_role: "workspace_admin" });
    await flush(0);
    const details = container.querySelector("details");
    expect(details?.textContent).toContain("Ajustes Avanzados para DBAs");
    expect(details?.open).toBe(false);
  });

  it("filters by classification without refetching", async () => {
    await renderPage();
    const select = [...container.querySelectorAll("label")]
      .find((node) => node.querySelector("span")?.textContent === "Clasificación")
      ?.querySelector("select") as HTMLSelectElement;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set?.call(select, "financial");
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const titles = [...container.querySelectorAll("li h3")].map((node) => node.textContent);
    expect(titles).toEqual(["Empleados"]);
    expect(client.getDataCatalog).toHaveBeenCalledTimes(1);
  });
});
