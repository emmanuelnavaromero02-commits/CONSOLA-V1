// @vitest-environment jsdom

import { act } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { activeEdges } from "@/lib/catalog/relations";

import { RelationsGraph } from "./RelationsGraph";
import { mount, type Mounted } from "./test-utils";

let view: Mounted;
const labelOf = (name: string) => ({ employees: "Empleados", departments: "Departamentos" } as Record<string, string>)[name] ?? name;

const EDGES = activeEdges([
  {
    from_dataset: "employees",
    from_column: "department_id",
    to_dataset: "departments",
    to_column: "department_id",
    cardinality: "N:1",
    origin: "copilot",
  },
  {
    from_dataset: "employees",
    from_column: "company",
    to_dataset: "companies",
    to_column: "company",
    cardinality: "1:1",
    origin: "manual",
  },
]);

beforeEach(() => {
  view = mount();
});

afterEach(async () => {
  await view.unmount();
});

describe("RelationsGraph", () => {
  it("draws a deterministic circle: dashed Copilot edges, solid manual ones, cardinality labels", async () => {
    await view.render(<RelationsGraph edges={EDGES} labelOf={labelOf} />);
    const svg = view.container.querySelector("svg[role='img']");
    expect(svg?.getAttribute("aria-label")).toBe("Grafo de relaciones: 3 tablas y 2 relaciones");
    expect(view.container.querySelector('[data-origin="copilot"] line')?.getAttribute("stroke-dasharray")).toBe("6 4");
    expect(view.container.querySelector('[data-origin="manual"] line')?.getAttribute("stroke-dasharray")).toBeNull();
    const labels = [...view.container.querySelectorAll("svg text")].map((node) => node.textContent);
    expect(labels).toContain("N:1");
    expect(labels).toContain("1:1");
    expect(labels).toContain("Empleados");
    const first = view.container.innerHTML;
    await view.render(<RelationsGraph edges={EDGES} labelOf={labelOf} />);
    expect(view.container.innerHTML).toBe(first);
  });

  it("zooms with the shared viewport helpers", async () => {
    await view.render(<RelationsGraph edges={EDGES} labelOf={labelOf} />);
    const zoomIn = view.container.querySelector<HTMLButtonElement>('button[aria-label="Acercar"]');
    await act(async () => {
      zoomIn?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(view.container.textContent).toContain("120%");
    expect(view.container.querySelector("svg g")?.getAttribute("transform")).toContain("scale(1.2)");
    const reset = view.container.querySelector<HTMLButtonElement>('button[aria-label="Ajustar a la vista"]');
    await act(async () => {
      reset?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(view.container.textContent).toContain("100%");
  });

  it("notes when only the top 40 tables are drawn", async () => {
    const many = activeEdges(
      Array.from({ length: 45 }, (_, index) => ({
        from_dataset: `t${String(index).padStart(2, "0")}`,
        from_column: "k",
        to_dataset: "hub",
        to_column: "k",
      })),
    );
    await view.render(<RelationsGraph edges={many} labelOf={labelOf} />);
    expect(view.container.textContent).toContain("Se muestran las 40 tablas con más relaciones de 46.");
    expect(view.container.querySelectorAll("svg[role='img'] circle")).toHaveLength(40);
  });
});
