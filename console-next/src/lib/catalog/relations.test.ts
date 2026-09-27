import { describe, expect, it } from "vitest";

import type { CatalogRelationship } from "@/lib/data/types";

import { activeEdges, cardinalitySentence, circularLayout, matrixKey, relationMatrix } from "./relations";

const RELATIONSHIPS: CatalogRelationship[] = [
  {
    from_dataset: "employees",
    from_column: "department_id",
    to_dataset: "departments",
    to_column: "department_id",
    cardinality: "N:1",
    origin: "copilot",
    status: "active",
    confidence: 0.95,
  },
  {
    from_dataset: "employees",
    from_column: "company",
    to_dataset: "companies",
    to_column: "company",
    origin: "manual",
  },
  {
    from_dataset: "employees",
    from_column: "location",
    to_dataset: "locations",
    to_column: "location",
    status: "rejected",
  },
  { from_dataset: "employees", from_column: "", to_dataset: "x", to_column: "y" },
];

describe("relations", () => {
  it("keeps active, complete and unique edges", () => {
    const edges = activeEdges([...RELATIONSHIPS, RELATIONSHIPS[0]]);
    expect(edges.map((edge) => `${edge.from}>${edge.to}:${edge.origin}`)).toEqual([
      "employees>departments:copilot",
      "employees>companies:manual",
    ]);
  });

  it("builds a matrix of referencing rows and referenced columns", () => {
    const matrix = relationMatrix(activeEdges(RELATIONSHIPS));
    expect(matrix.rows).toEqual(["employees"]);
    expect(matrix.columns).toEqual(["companies", "departments"]);
    expect(matrix.cells.get(matrixKey("employees", "departments"))?.[0].cardinality).toBe("N:1");
    expect(matrix.truncated).toBe(false);
  });

  it("lays nodes on a deterministic circle and caps them honestly", () => {
    const many: CatalogRelationship[] = Array.from({ length: 50 }, (_, index) => ({
      from_dataset: `t${String(index).padStart(2, "0")}`,
      from_column: "k",
      to_dataset: "hub",
      to_column: "k",
    }));
    const layout = circularLayout(activeEdges(many), 40, 640);
    expect(layout.nodes).toHaveLength(40);
    expect(layout.total).toBe(51);
    expect(layout.truncated).toBe(true);
    expect(layout.nodes[0]).toEqual({ id: "hub", degree: 50, x: 320, y: 110 });
    expect(circularLayout(activeEdges(many), 40, 640)).toEqual(layout);
  });

  it("describes cardinality in business Spanish", () => {
    const [edge] = activeEdges(RELATIONSHIPS);
    const labels: Record<string, string> = { employees: "Empleados", departments: "Departamentos" };
    expect(cardinalitySentence(edge, (name) => labels[name] ?? name)).toBe(
      "Cada registro de Empleados se vincula con un registro de Departamentos (N:1).",
    );
    expect(cardinalitySentence({ ...edge, cardinality: "1:1" })).toContain("corresponde a un registro");
    expect(cardinalitySentence({ ...edge, cardinality: null })).toBe("employees se vincula con departments.");
  });
});
