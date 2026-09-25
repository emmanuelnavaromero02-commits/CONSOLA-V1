import type { SapB1LoadRow, SapB1MappingEntity } from "./types";

export const FINANCE_RUN_HEADER = ["indicador", "empresa", "mes", "dimension", "clave", "valor", "unidad"] as const;
export const FINANCE_RUN_MAX_BYTES = 5 * 1024 * 1024;

export const MAPPING_CSV_HEADER = [
  "tabla_sap",
  "nombre_de_negocio",
  "descripcion",
  "modo",
  "campo_de_fecha",
  "llave_primaria",
  "empresa",
  "ventana_desde",
  "ventana_hasta",
  "campos",
  "datasets",
  "filas_en_origen",
  "filas_en_plataforma",
  "porcentaje_cargado",
  "estado",
  "contado_en",
] as const;

type Cell = string | number | boolean | null | undefined;

export function csvCell(value: Cell): string {
  if (value === null || value === undefined) return "";
  let text = typeof value === "number" ? (Number.isFinite(value) ? String(value) : "") : String(value);
  if (/^[=+@\t\r]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function toCsv(rows: ReadonlyArray<ReadonlyArray<Cell>>): string {
  return `${rows.map((row) => row.map(csvCell).join(",")).join("\r\n")}\r\n`;
}

export function financeRunTemplate(): string {
  return toCsv([FINANCE_RUN_HEADER]);
}

export function utf8Bytes(text: string): number {
  return new TextEncoder().encode(text).length;
}

export interface MappingRow {
  entity: SapB1MappingEntity;
  loads: SapB1LoadRow[];
}

export function mergeMapping(entities: SapB1MappingEntity[], loads: SapB1LoadRow[] | null | undefined): MappingRow[] {
  const byEntity = new Map<string, SapB1LoadRow[]>();
  for (const row of loads ?? []) {
    const list = byEntity.get(row.entity) ?? [];
    list.push(row);
    byEntity.set(row.entity, list);
  }
  const rows = entities.map((entity) => ({
    entity,
    loads: (byEntity.get(entity.entity) ?? []).sort((a, b) => a.company.localeCompare(b.company)),
  }));
  const known = new Set(entities.map((entity) => entity.entity));
  for (const [name, list] of byEntity) {
    if (known.has(name)) continue;
    const first = list[0];
    rows.push({
      entity: { entity: name, business_name: first?.business_name ?? null, mode: first?.mode ?? null, fields: [], datasets: [] },
      loads: list.sort((a, b) => a.company.localeCompare(b.company)),
    });
  }
  return rows;
}

export function mappingCsv(rows: MappingRow[]): string {
  const lines: Cell[][] = [[...MAPPING_CSV_HEADER]];
  for (const { entity, loads } of rows) {
    const base: Cell[] = [
      entity.entity,
      entity.business_name,
      entity.description,
      entity.mode,
      entity.date_field,
      entity.primary_key,
    ];
    const tail: Cell[] = [entity.fields.join(" "), entity.datasets.join(" ")];
    if (!loads.length) {
      lines.push([...base, "", "", "", ...tail, "", "", "", "sin_conteo", ""]);
      continue;
    }
    for (const load of loads) {
      lines.push([
        ...base,
        load.company,
        load.dated ? load.window_start : "",
        load.dated ? load.window_end : "",
        ...tail,
        load.source_rows,
        load.platform_rows,
        load.loaded_pct,
        load.status,
        load.counted_at,
      ]);
    }
  }
  return toCsv(lines);
}

export function downloadText(filename: string, text: string, type = "text/csv;charset=utf-8"): void {
  const blob = new Blob(["﻿", text], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
