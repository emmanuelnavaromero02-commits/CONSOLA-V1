// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { activeEdges } from "@/lib/catalog/relations";

import { AdvancedDbaSettings, CARDINALITIES, JOIN_TYPES, canManageCatalog } from "./AdvancedDbaSettings";
import { mount, type Mounted } from "./test-utils";

const access = vi.hoisted(() => ({ getMeAccess: vi.fn() }));
const client = vi.hoisted(() => ({
  getDatasetRows: vi.fn(),
  registerCatalogRelationship: vi.fn(),
  rejectCatalogRelationship: vi.fn(),
  upsertCatalogEntry: vi.fn(),
}));

vi.mock("@/lib/admin-surfaces", () => access);
vi.mock("@/lib/data/client", () => client);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

let view: Mounted;

const DATASETS = {
  employees: {
    layer: "silver",
    display_name: "Empleados",
    columns: [{ name: "employee_id", type: "BIGINT" }],
  },
  departments: { layer: "gold", display_name: "Departamentos", columns: [] },
};
const EDGES = activeEdges([
  {
    from_dataset: "employees",
    from_column: "department_id",
    to_dataset: "departments",
    to_column: "department_id",
    cardinality: "N:1",
    origin: "copilot",
  },
]);
const labelOf = (name: string) => (DATASETS as Record<string, { display_name: string }>)[name]?.display_name ?? name;

async function renderWith(role: { global?: string; workspace_role?: string }) {
  access.getMeAccess.mockResolvedValue({
    role: { global: role.global ?? "user" },
    workspace: { workspace_role: role.workspace_role ?? "viewer" },
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await view.render(
    <QueryClientProvider client={queryClient}>
      <AdvancedDbaSettings datasets={DATASETS} edges={EDGES} labelOf={labelOf} />
    </QueryClientProvider>,
  );
  for (let tick = 0; tick < 5; tick += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function change(element: HTMLInputElement | HTMLSelectElement | null, value: string) {
  expect(element).toBeTruthy();
  await act(async () => {
    const proto = element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(element, value);
    element?.dispatchEvent(new Event(element instanceof HTMLSelectElement ? "change" : "input", { bubbles: true }));
  });
}

function field(label: string, root: ParentNode = view.container) {
  const span = [...root.querySelectorAll("label > span")].find((node) => node.textContent === label);
  return span?.parentElement?.querySelector("input, select") as HTMLInputElement | HTMLSelectElement | null;
}

beforeEach(() => {
  view = mount();
  vi.clearAllMocks();
});

afterEach(async () => {
  await view.unmount();
});

describe("AdvancedDbaSettings", () => {
  it("stays hidden for viewers", async () => {
    await renderWith({ global: "user", workspace_role: "viewer" });
    expect(view.container.textContent).toBe("");
  });

  it("is a collapsed accordion for workspace admins with join type and cardinality apart", async () => {
    await renderWith({ workspace_role: "workspace_admin" });
    const details = view.container.querySelector("details");
    expect(details).toBeTruthy();
    expect(details?.open).toBe(false);
    expect(view.container.querySelector("summary")?.textContent).toContain("Ajustes Avanzados para DBAs");
    expect(field("Tipo de unión")?.tagName).toBe("SELECT");
    expect(field("Cardinalidad")?.tagName).toBe("SELECT");
    const joinValues = [...(field("Tipo de unión") as HTMLSelectElement).options].map((option) => option.value);
    expect(joinValues).toEqual(JOIN_TYPES.map((option) => option.value));
    expect(joinValues).not.toContain("many_to_one");
    expect(CARDINALITIES.map((option) => option.value)).toEqual(["", "N:1", "1:1", "1:N", "N:N"]);
  });

  it("registers a relationship with separate join type and cardinality", async () => {
    client.registerCatalogRelationship.mockResolvedValue({ registered: true });
    await renderWith({ global: "admin" });
    await change(field("Tabla origen"), "employees");
    await change(field("Columna origen"), "company");
    await change(field("Tabla destino"), "departments");
    await change(field("Columna destino"), "company");
    await change(field("Tipo de unión"), "INNER");
    await change(field("Cardinalidad"), "1:1");
    const save = [...view.container.querySelectorAll("button")].find((node) => node.textContent?.includes("Guardar relación"));
    await act(async () => {
      save?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(client.registerCatalogRelationship).toHaveBeenCalledWith({
      from_dataset: "employees",
      from_column: "company",
      to_dataset: "departments",
      to_column: "company",
      join_hint: "INNER",
      cardinality: "1:1",
      description: undefined,
    });
  });

  it("lets a DBA reject a Copilot relationship and see raw types", async () => {
    client.rejectCatalogRelationship.mockResolvedValue({ rejected: true });
    await renderWith({ global: "admin" });
    expect(view.container.textContent).toContain(
      "Cada registro de Empleados se vincula con un registro de Departamentos (N:1).",
    );
    const reject = [...view.container.querySelectorAll("button")].find((node) =>
      node.textContent?.includes("Rechazar relación del Copiloto"),
    );
    await act(async () => {
      reject?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(client.rejectCatalogRelationship).toHaveBeenCalledWith({
      from_dataset: "employees",
      from_column: "department_id",
      to_dataset: "departments",
      to_column: "department_id",
    });
    const selects = [...view.container.querySelectorAll("label > span")].filter((node) => node.textContent === "Tabla");
    await change(selects[1]?.parentElement?.querySelector("select") ?? null, "employees");
    expect(view.container.textContent).toContain("BIGINT");
  });

  it("recognises the roles the backend accepts", () => {
    expect(canManageCatalog(undefined)).toBe(false);
    expect(canManageCatalog({ role: { global: "admin" } })).toBe(true);
    expect(canManageCatalog({ workspace: { workspace_role: "workspace_admin" } })).toBe(true);
    expect(canManageCatalog({ role: { global: "user" }, workspace: { workspace_role: "editor" } })).toBe(false);
  });
});
