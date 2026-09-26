import { describe, expect, it } from "vitest";

import { csvFilename, formatCell, pageCount, paginate, rowsToCsv, sortRows, tableSummary } from "./table";

describe("table helpers", () => {
  it("formats cells as text", () => {
    expect(formatCell(null)).toBe("");
    expect(formatCell(undefined)).toBe("");
    expect(formatCell("a")).toBe("a");
    expect(formatCell(3.5)).toBe("3.5");
    expect(formatCell(false)).toBe("false");
    expect(formatCell({ a: 1 })).toBe('{"a":1}');
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    expect(formatCell(cyclic)).toBe("[object Object]");
  });

  it("sorts numbers and numeric strings numerically, text in Spanish order, blanks last", () => {
    const rows = [
      { id: "10", name: "Ñandú" },
      { id: 2, name: "zeta" },
      { id: null, name: "" },
      { id: "1", name: "árbol" },
      { id: "", name: "beta" },
      { id: 30, name: null },
    ];
    expect(sortRows(rows, "id", "asc").map((row) => row.id)).toEqual(["1", 2, "10", 30, null, ""]);
    expect(sortRows(rows, "id", "desc").map((row) => row.id)).toEqual([30, "10", 2, "1", null, ""]);
    expect(sortRows(rows, "name", "asc").map((row) => row.name)).toEqual(["árbol", "beta", "Ñandú", "zeta", "", null]);
    expect(sortRows(rows, null, "asc")).toEqual(rows);
  });

  it("keeps equal rows in their original order", () => {
    const rows = [
      { k: "a", n: 1 },
      { k: "b", n: 1 },
      { k: "c", n: 0 },
    ];
    expect(sortRows(rows, "n", "asc").map((row) => row.k)).toEqual(["c", "a", "b"]);
    expect(sortRows(rows, "n", "desc").map((row) => row.k)).toEqual(["a", "b", "c"]);
  });

  it("paginates", () => {
    const rows = Array.from({ length: 30 }, (_, index) => index);
    expect(pageCount(30, 25)).toBe(2);
    expect(pageCount(0, 25)).toBe(1);
    expect(paginate(rows, 1, 25)).toEqual([25, 26, 27, 28, 29]);
  });

  it("summarises the visible range with the real total when the API gives it", () => {
    expect(tableSummary({ start: 0, end: 0, loaded: 0 })).toBe("Sin registros");
    expect(tableSummary({ start: 1, end: 25, loaded: 30 })).toBe("Mostrando 1–25 de 30 registros");
    expect(tableSummary({ start: 1, end: 1, loaded: 1 })).toBe("Mostrando 1–1 de 1 registro");
    expect(tableSummary({ start: 1, end: 25, loaded: 30, total: 1240 })).toBe(
      "Mostrando 1–25 de 1,240 registros · 30 cargados en la vista previa",
    );
    expect(tableSummary({ start: 1, end: 1, loaded: 1, total: 2 })).toBe(
      "Mostrando 1–1 de 2 registros · 1 cargado en la vista previa",
    );
    expect(tableSummary({ start: 1, end: 25, loaded: 30, total: 30 })).toBe("Mostrando 1–25 de 30 registros");
  });

  it("exports CSV with the formula guard", () => {
    expect(rowsToCsv(["id", "name", "meta"], [{ id: 1, name: "=cmd", meta: { a: 1 } }, { id: 2, name: null }])).toBe(
      'id,name,meta\r\n1,\'=cmd,"{""a"":1}"\r\n2,,\r\n',
    );
  });

  it("builds safe CSV file names", () => {
    expect(csvFilename("orders")).toBe("orders.csv");
    expect(csvFilename("gold ventas/2026")).toBe("gold_ventas_2026.csv");
    expect(csvFilename("../../etc")).toBe("etc.csv");
    expect(csvFilename("")).toBe("vista-previa.csv");
    expect(csvFilename("datos.CSV")).toBe("datos.csv");
  });
});
