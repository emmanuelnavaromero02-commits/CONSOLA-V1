import { describe, expect, it } from "vitest";

import {
  BRONZE_ROW_CAP,
  DATASET_ROW_CAP,
  EMPTY_SPEC,
  buildExploreRequest,
  clampRows,
  emptyFilter,
  rowCapFor,
  sourceFromKey,
  sourceKey,
  specIsEmpty,
  specProblems,
  splitListValue,
  type ExplorerColumn,
  type ExplorerSpec,
} from "./spec";

const COLUMNS: ExplorerColumn[] = [
  { name: "nombre", type: "VARCHAR", kind: "text" },
  { name: "salario", type: "DOUBLE", kind: "number" },
  { name: "meta", type: "STRUCT(a INTEGER)", kind: "other" },
];
const BRONZE = { kind: "bronze" as const, cartridge: "acme", entity: "Employee" };

function spec(patch: Partial<ExplorerSpec>): ExplorerSpec {
  return { ...EMPTY_SPEC, ...patch };
}

describe("explorer spec helpers", () => {
  it("round-trips logical source keys and rejects anything else", () => {
    expect(sourceKey(BRONZE)).toBe("raw/acme/Employee");
    expect(sourceKey({ kind: "dataset", name: "ventas" })).toBe("gold/ventas");
    expect(sourceKey(null)).toBe("");
    expect(sourceFromKey("raw/acme/Employee")).toEqual(BRONZE);
    expect(sourceFromKey("gold/ventas")).toEqual({ kind: "dataset", name: "ventas" });
    for (const bad of ["raw/acme", "raw/acme/E/x", "s3://b/raw/a/E", "raw/../E/x", "silver/a/b", "gold/x;drop"]) {
      expect(sourceFromKey(bad)).toBeNull();
    }
  });

  it("clamps rows to the per-source caps without raising them", () => {
    expect(rowCapFor(BRONZE)).toBe(BRONZE_ROW_CAP);
    expect(rowCapFor({ kind: "dataset", name: "x" })).toBe(DATASET_ROW_CAP);
    expect(clampRows(5000, BRONZE_ROW_CAP)).toBe(2000);
    expect(clampRows(0, BRONZE_ROW_CAP)).toBe(1);
    expect(clampRows("abc", BRONZE_ROW_CAP)).toBe(50);
    expect(clampRows("12.7", BRONZE_ROW_CAP)).toBe(12);
  });

  it("splits list values and removes duplicates", () => {
    expect(splitListValue("Ventas, Finanzas; Ventas\n RH ")).toEqual(["Ventas", "Finanzas", "RH"]);
  });

  it("starts filters with an operator that fits the column type", () => {
    expect(emptyFilter(COLUMNS).op).toBe("contains");
    expect(emptyFilter(COLUMNS, "salario").op).toBe("eq");
    expect(emptyFilter(COLUMNS, "meta").op).toBe("is_not_empty");
    expect(emptyFilter([]).column).toBe("");
  });

  it("lists incomplete filters in Spanish instead of guessing", () => {
    const problems = specProblems(
      spec({
        filters: [
          { id: "a", column: "", op: "eq", value: "", valueTo: "" },
          { id: "b", column: "nombre", op: "gt", value: "1", valueTo: "" },
          { id: "c", column: "salario", op: "eq", value: " ", valueTo: "" },
          { id: "d", column: "salario", op: "between", value: "1", valueTo: "" },
          { id: "e", column: "nombre", op: "in", value: " , ", valueTo: "" },
          { id: "f", column: "nombre", op: "is_empty", value: "", valueTo: "" },
        ],
        sort: [
          { column: "nombre", direction: "asc" },
          { column: "nombre", direction: "desc" },
          { column: "zzz", direction: "asc" },
        ],
      }),
      COLUMNS,
    );
    expect(problems).toEqual([
      "Filtro 1: elige una columna.",
      "Filtro 2: «es mayor que» no aplica a «nombre» (texto).",
      "Filtro 3: escribe un valor.",
      "Filtro 4: «está entre» necesita dos valores.",
      "Filtro 5: escribe al menos un valor.",
      "Orden 2: «nombre» está repetida.",
      "Orden 3: elige una columna.",
    ]);
  });

  it("builds the structured request the backend compiles", () => {
    const request = buildExploreRequest(
      BRONZE,
      spec({
        columns: ["nombre"],
        filters: [
          { id: "a", column: "nombre", op: "contains", value: " Ana ", valueTo: "" },
          { id: "b", column: "salario", op: "between", value: "1", valueTo: "9" },
          { id: "c", column: "nombre", op: "in", value: "a, b", valueTo: "" },
          { id: "d", column: "meta", op: "is_empty", value: "ignored", valueTo: "" },
        ],
        sort: [{ column: "salario", direction: "desc" }],
        limit: 99999,
        latestOnly: true,
      }),
      { execute: true },
    );
    expect(request).toEqual({
      source: BRONZE,
      columns: ["nombre"],
      filters: [
        { column: "nombre", op: "contains", value: "Ana" },
        { column: "salario", op: "between", values: ["1", "9"] },
        { column: "nombre", op: "in", values: ["a", "b"] },
        { column: "meta", op: "is_empty" },
      ],
      sort: [{ column: "salario", direction: "desc" }],
      limit: 2000,
      latest_only: true,
      execute: true,
    });
    expect(JSON.stringify(request)).not.toMatch(/select|sql/i);
  });

  it("keeps pre-structured in-values intact even when they contain commas", () => {
    const request = buildExploreRequest(
      { kind: "dataset", name: "ventas" },
      spec({
        filters: [{
          id: "nl1",
          column: "nombre",
          op: "in",
          value: "García, Juan, Ana",
          valueTo: "",
          values: ["García, Juan", "Ana"],
        }],
      }),
      { execute: true },
    );
    expect(request.filters).toEqual([
      { column: "nombre", op: "in", values: ["García, Juan", "Ana"] },
    ]);
  });

  it("never asks gold datasets for the latest bronze load", () => {
    const request = buildExploreRequest({ kind: "dataset", name: "ventas" }, spec({ latestOnly: true, limit: 50000 }), { execute: false });
    expect(request.latest_only).toBe(false);
    expect(request.limit).toBe(10000);
    expect(request.execute).toBe(false);
  });

  it("detects an empty spec", () => {
    expect(specIsEmpty(EMPTY_SPEC)).toBe(true);
    expect(specIsEmpty(spec({ latestOnly: true }))).toBe(false);
  });
});

describe("mergeParsedSpec", () => {
  it("replaces only the parts the sentence mentioned", async () => {
    const { mergeParsedSpec } = await import("./spec");
    const base = spec({
      columns: ["nombre"],
      filters: [{ id: "old", column: "nombre", op: "contains", value: "x", valueTo: "" }],
      sort: [{ column: "nombre", direction: "asc" }],
      limit: 30,
    });
    const onlyLimit = mergeParsedSpec(base, { filters: [], sort: [], limit: 10, latestOnly: false, unrecognized: [], notes: [] });
    expect(onlyLimit.filters).toEqual(base.filters);
    expect(onlyLimit.sort).toEqual(base.sort);
    expect(onlyLimit.limit).toBe(10);
    const replaced = mergeParsedSpec(base, {
      filters: [{ column: "salario", op: "gt", value: "5", valueTo: "" }],
      sort: [{ column: "salario", direction: "desc" }],
      limit: null,
      latestOnly: true,
      unrecognized: [],
      notes: [],
    });
    expect(replaced.filters).toHaveLength(1);
    expect(replaced.filters[0]).toMatchObject({ column: "salario", op: "gt", value: "5" });
    expect(replaced.filters[0].id).not.toBe("old");
    expect(replaced.sort).toEqual([{ column: "salario", direction: "desc" }]);
    expect(replaced.limit).toBe(30);
    expect(replaced.latestOnly).toBe(true);
    expect(replaced.columns).toEqual(["nombre"]);
  });
});
