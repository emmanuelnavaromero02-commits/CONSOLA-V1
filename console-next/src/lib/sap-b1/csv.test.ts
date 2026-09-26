import { describe, expect, it } from "vitest";

import { csvCell, financeRunTemplate, mappingCsv, MAPPING_CSV_HEADER, mergeMapping, toCsv, utf8Bytes } from "./csv";
import type { SapB1LoadRow, SapB1MappingEntity } from "./types";

const entities: SapB1MappingEntity[] = [
  {
    entity: "OINV",
    business_name: "Facturas de clientes",
    description: "Encabezado, con \"comillas\", y comas",
    mode: "incremental",
    date_field: "DocDate",
    primary_key: "DocEntry",
    fields: ["DocEntry", "DocDate"],
    datasets: ["sap_b1_ar_invoice_lines"],
  },
  { entity: "OCRN", business_name: "Monedas", mode: "full", fields: ["CurrCode"], datasets: [] },
];

const loads: SapB1LoadRow[] = [
  { company: "us", entity: "OINV", dated: true, window_start: "2024-09-01", window_end: "2026-09-01", source_rows: 10, platform_rows: 8, loaded_pct: 80, status: "faltan", counted_at: "2026-09-25T06:00:00Z" },
  { company: "mx", entity: "OINV", dated: true, window_start: "2024-09-01", window_end: "2026-09-01", source_rows: 5, platform_rows: 5, loaded_pct: 100, status: "ok", counted_at: "2026-09-25T06:00:00Z" },
  { company: "mx", entity: "WTR1", business_name: "Líneas de traslados", mode: "incremental", source_rows: 3, platform_rows: 0, loaded_pct: 0, status: "faltan" },
];

describe("sap-b1 csv helpers", () => {
  it("generates the finance run template with the header row only", () => {
    expect(financeRunTemplate()).toBe("indicador,empresa,mes,dimension,clave,valor,unidad\r\n");
  });

  it("escapes quotes, separators and spreadsheet formulas", () => {
    expect(csvCell('a "b", c')).toBe('"a ""b"", c"');
    expect(csvCell("=SUM(A1)")).toBe("'=SUM(A1)");
    expect(csvCell("@cmd")).toBe("'@cmd");
    expect(csvCell(-12.5)).toBe("-12.5");
    expect(csvCell(Number.NaN)).toBe("");
    expect(csvCell(null)).toBe("");
    expect(toCsv([["a", 1], ["b", null]])).toBe("a,1\r\nb,\r\n");
    expect(utf8Bytes("ñ")).toBe(2);
  });

  it("merges the mapping with the load counts per company and keeps counted tables the catalog lacks", () => {
    const rows = mergeMapping(entities, loads);
    expect(rows.map((row) => row.entity.entity)).toEqual(["OINV", "OCRN", "WTR1"]);
    expect(rows[0].loads.map((load) => load.company)).toEqual(["mx", "us"]);
    expect(rows[1].loads).toEqual([]);
    expect(rows[2].entity.business_name).toBe("Líneas de traslados");
    expect(mergeMapping(entities, null).every((row) => row.loads.length === 0)).toBe(true);
  });

  it("exports one CSV line per table and company for signature", () => {
    const csv = mappingCsv(mergeMapping(entities, loads));
    const lines = csv.trimEnd().split("\r\n");
    expect(lines[0]).toBe(MAPPING_CSV_HEADER.join(","));
    expect(lines).toHaveLength(5);
    expect(lines[1]).toBe(
      'OINV,Facturas de clientes,"Encabezado, con ""comillas"", y comas",incremental,DocDate,DocEntry,mx,2024-09-01,2026-09-01,DocEntry DocDate,sap_b1_ar_invoice_lines,5,5,100,ok,2026-09-25T06:00:00Z',
    );
    expect(lines[3]).toBe("OCRN,Monedas,,full,,,,,,CurrCode,,,,,sin_conteo,");
    expect(lines[4]).toContain("WTR1,Líneas de traslados,,incremental,,,mx,,,,,3,0,0,faltan,");
  });
});
