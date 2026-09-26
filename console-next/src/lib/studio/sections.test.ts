import { describe, expect, it } from "vitest";

import {
  isSectionId,
  quickPromptsForStep,
  sectionCounts,
  sectionForStep,
  sectionHint,
  STUDIO_SECTIONS,
} from "./sections";
import type { StudioManifest } from "./types";

const SAP_LIKE: StudioManifest = {
  id: "erp",
  name: "SAP Business One",
  entities: [
    { entity: "OCRD", display_name: "Business partners (OCRD)", description: "Customers (CardType C) and suppliers." },
    { entity: "OINV", display_name: "A/R invoices (OINV)" },
    { entity: "ORIN", display_name: "A/R credit memos (ORIN)" },
    { entity: "OSLP", display_name: "Sales employees (OSLP)" },
  ],
};

const OTHER: StudioManifest = {
  id: "psa",
  name: "Replicon PSA",
  entities: [
    { entity: "TimeEntry", display_name: "Registro de horas" },
    { entity: "Project", display_name: "Proyectos" },
  ],
};

describe("Studio sections", () => {
  it("keeps the ids, steps and business labels in order", () => {
    expect(STUDIO_SECTIONS.map((section) => [section.id, section.label, section.step])).toEqual([
      ["grafo", "Mapa del Flujo", 1],
      ["dags", "Automatizaciones", 2],
      ["entidades", "Tablas de Origen (Bronce)", 3],
      ["refinar", "Modelado y Limpieza (Plata)", 4],
      ["capas", "Indicadores y KPIs (Oro)", 5],
    ]);
    expect(isSectionId("capas")).toBe(true);
    expect(isSectionId("Capas")).toBe(false);
    expect(isSectionId(null)).toBe(false);
    expect(sectionForStep(4).id).toBe("refinar");
    expect(sectionForStep(9).id).toBe("grafo");
  });

  it("explains each section and names the real source system", () => {
    expect(sectionHint("grafo")).toBe(
      "Vista visual de cómo viaja la información desde el origen hasta los reportes finales",
    );
    expect(sectionHint("dags")).toBe("Procesos programados que extraen y actualizan los datos automáticamente");
    expect(sectionHint("entidades", { sourceName: "SAP Business One" })).toBe(
      "Datos crudos extraídos directamente de SAP Business One",
    );
    expect(sectionHint("entidades", { sourceName: "  " })).toBe(
      "Datos crudos extraídos directamente de los sistemas fuente",
    );
    expect(sectionHint("refinar")).toBe("Transformaciones y reglas de negocio para estandarizar y validar los datos");
    expect(sectionHint("capas")).toBe("Tablas consolidadas listas para dashboards, análisis y decisiones directivas");
  });

  it("counts from the manifest, the graph and the datasets, or reports nothing", () => {
    const counts = sectionCounts({
      cartridge: "acme",
      graph: {
        nodes: [
          { id: "cartridge:acme", kind: "cartridge" },
          { id: "entity:Invoice", kind: "entity" },
          { id: "entity:Invoice", kind: "entity" },
        ],
        edges: [],
      },
      manifest: {
        id: "acme",
        dags: [{ dag_id: "acme_a" }, "acme_b"],
        entities: [{ entity: "Invoice", dag_id: "acme_a" }, { entity: "Customer", dag_id: "acme_c" }],
      },
      datasets: [
        { name: "orders", layer: "silver", cartridge: "acme" },
        { name: "shared", layer: "SILVER" },
        { name: "sales", layer: "gold", cartridge: "acme" },
        { name: "foreign", layer: "gold", cartridge: "beta" },
      ],
    });
    expect(counts).toEqual({ grafo: 2, dags: 3, entidades: 2, refinar: 2, capas: 1 });
    expect(sectionCounts({ cartridge: "acme", graph: undefined, manifest: undefined, datasets: undefined })).toEqual({
      grafo: null,
      dags: null,
      entidades: null,
      refinar: null,
      capas: null,
    });
  });

  it("uses the brief's prompts when the cartridge has those tables", () => {
    expect(quickPromptsForStep(1, SAP_LIKE)).toEqual([
      "Resume cómo viajan los datos de este cartucho desde el origen hasta los indicadores",
      "¿Qué le falta a este cartucho para estar listo?",
    ]);
    expect(quickPromptsForStep(2, SAP_LIKE)).toEqual([
      "¿Por qué falló la última extracción?",
      "¿Cómo cambio la frecuencia a diaria?",
    ]);
    expect(quickPromptsForStep(3, SAP_LIKE)).toEqual([
      "¿Qué campos incluye la tabla de Clientes?",
      "¿Hay registros duplicados en facturas?",
    ]);
    expect(quickPromptsForStep(4, SAP_LIKE)).toEqual([
      "Ayúdame a calcular el margen neto restando notas de crédito",
      "¿Cómo agrupo las ventas por vendedor?",
    ]);
    expect(quickPromptsForStep(5, SAP_LIKE)).toEqual([
      "¿Qué dashboards consumen este dataset?",
      "Muéstrame, sin publicar, una vista previa del ranking de clientes",
    ]);
  });

  it("never names tables the cartridge does not have", () => {
    expect(quickPromptsForStep(3, OTHER)).toEqual([
      "¿Qué campos incluye la tabla Registro de horas?",
      "¿Hay registros duplicados en Proyectos?",
    ]);
    expect(quickPromptsForStep(4, OTHER)).toEqual([
      "¿Qué reglas de limpieza conviene aplicar a Registro de horas?",
      "¿Cómo resumo Proyectos en un dataset Plata?",
    ]);
    expect(quickPromptsForStep(5, OTHER)).toEqual([
      "¿Qué dashboards consumen este dataset?",
      "Muéstrame, sin publicar, una vista previa de un indicador Oro de este cartucho",
    ]);
    expect(quickPromptsForStep(3, null)).toEqual([
      "¿Qué campos incluyen las tablas de origen de este cartucho?",
      "¿Hay registros duplicados en las tablas de origen?",
    ]);
    expect(quickPromptsForStep(4, { id: "x", entities: [] })).toEqual([
      "¿Qué reglas de limpieza conviene aplicar a las tablas de origen?",
      "¿Cómo resumo las tablas de origen en un dataset Plata?",
    ]);
    expect(quickPromptsForStep(7, OTHER)).toEqual([]);
    const all = [1, 2, 3, 4, 5].flatMap((step) => quickPromptsForStep(step, OTHER)).join(" ");
    expect(all).not.toMatch(/Clientes|facturas|notas de crédito|vendedor/);
  });
});
