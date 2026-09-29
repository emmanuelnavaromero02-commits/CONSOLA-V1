import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const read = (path: string) => readFileSync(join(process.cwd(), path), "utf8");
const viewer = read("src/app/(shell)/viewer/page.tsx");
const hub = read("src/components/data/DataTechnicalHub.tsx");

const VIEWER_LABELS = [
  "Tareas de Extracción",
  "Flujo de Datos",
  "Última Actualización",
  "Glosario de Negocio",
  "Tablas de Datos",
  "Estructura y Campos",
  "Origen y Trazabilidad",
  "Bóveda de Accesos Seguros",
];

describe("viewer business labels", () => {
  it("pins the eight viewer sections in business Spanish", () => {
    for (const label of VIEWER_LABELS) {
      expect(viewer, label).toContain(label);
    }
  });

  it("mirrors the viewer labels in the technical hub", () => {
    for (const label of ["Flujo de Datos", "Última Actualización", "Glosario de Negocio", "Tablas de Datos", "Estructura y Campos", "Origen y Trazabilidad"]) {
      expect(hub, label).toContain(label);
    }
  });

  it("keeps the internal headings in Spanish", () => {
    for (const heading of ["Corte", "Lote", "Almacenamiento", "Conexión", "Dirección", "Autenticación", "Llave", "Herramienta", "Argumentos", "Registros", "Vista previa", "Monitoreo"]) {
      expect(viewer, heading).toContain(heading);
    }
  });

  it("does not resurrect the English section labels", () => {
    for (const needle of [
      'label: "Jobs"',
      'label: "Pipeline"',
      'label: "Watermarks"',
      'label: "Semantic"',
      'label: "Datasets"',
      'label: "Schema"',
      'label: "Lineage"',
      'label: "Vault"',
      ">Watermark<",
      ">Batch<",
      ">Storage<",
      ">Conn ID<",
      ">Base URL<",
      ">Auth<",
      ">Key<",
      ">Logs<",
      ">Preview<",
      ">Tool<",
    ]) {
      expect(viewer, needle).not.toContain(needle);
    }
  });
});
