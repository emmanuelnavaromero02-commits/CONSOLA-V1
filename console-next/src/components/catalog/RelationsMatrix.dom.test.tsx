// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { activeEdges } from "@/lib/catalog/relations";

import { RelationsMatrix } from "./RelationsMatrix";
import { mount, type Mounted } from "./test-utils";

let view: Mounted;
const LABELS: Record<string, string> = { employees: "Empleados", departments: "Departamentos", companies: "Compañías" };
const labelOf = (name: string) => LABELS[name] ?? name;

beforeEach(() => {
  view = mount();
});

afterEach(async () => {
  await view.unmount();
});

describe("RelationsMatrix", () => {
  it("draws referencing rows against referenced columns with cardinality", async () => {
    const edges = activeEdges([
      {
        from_dataset: "employees",
        from_column: "department_id",
        to_dataset: "departments",
        to_column: "department_id",
        cardinality: "N:1",
        origin: "copilot",
        confidence: 0.95,
        basis: ["name:exact", "containment:1.00"],
      },
      {
        from_dataset: "employees",
        from_column: "company",
        to_dataset: "companies",
        to_column: "company",
        cardinality: "N:1",
        origin: "manual",
      },
    ]);
    await view.render(<RelationsMatrix edges={edges} labelOf={labelOf} />);
    const headers = [...view.container.querySelectorAll("thead th")].map((node) => node.textContent);
    expect(headers).toEqual(["Tabla / Referencia", "Compañías", "Departamentos"]);
    const copilot = view.container.querySelector('[data-origin="copilot"]');
    expect(copilot?.className).toContain("border-dashed");
    expect(copilot?.getAttribute("title")).toContain("Detectada por el Copiloto");
    expect(copilot?.getAttribute("title")).toContain("100% de los valores existen en la tabla destino");
    expect(view.container.querySelector('[data-origin="manual"]')?.className).toContain("border-solid");
    expect(view.container.textContent).toContain("N:1");
  });

  it("is honest when there is nothing or when it truncates", async () => {
    await view.render(<RelationsMatrix edges={[]} labelOf={labelOf} />);
    expect(view.container.textContent).toContain("Sin relaciones detectadas todavía.");
    const many = activeEdges(
      Array.from({ length: 5 }, (_, index) => ({
        from_dataset: `t${index}`,
        from_column: "k",
        to_dataset: "hub",
        to_column: "k",
      })),
    );
    await view.render(<RelationsMatrix edges={many} labelOf={labelOf} max={3} />);
    expect(view.container.textContent).toContain("Se muestran las 3 tablas con más relaciones de 6.");
  });
});
