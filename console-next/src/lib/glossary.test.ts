import { describe, expect, it } from "vitest";

import { dataSourceName, FORBIDDEN_UI_TERMS, forbiddenTermsIn, GLOSSARY, LAYER_LABELS, term } from "./glossary";

describe("glossary", () => {
  it("fixes the business vocabulary", () => {
    expect(GLOSSARY.cartridge).toEqual({ one: "Fuente de datos", other: "Fuentes de datos" });
    expect(GLOSSARY.tools.other).toBe("Herramientas");
    expect(GLOSSARY.knowledge.one).toBe("Conocimiento");
    expect(GLOSSARY.schedule.one).toBe("Frecuencia");
    expect(LAYER_LABELS).toEqual({
      bronze: "Tablas de Origen (Bronce)",
      silver: "Modelado y Limpieza (Plata)",
      gold: "Indicadores y KPIs (Oro)",
    });
  });

  it("picks singular or plural and lowercases on request", () => {
    expect(term("cartridge")).toBe("Fuente de datos");
    expect(term("cartridge", { count: 3 })).toBe("Fuentes de datos");
    expect(term("cartridge", { count: 0, lower: true })).toBe("fuentes de datos");
    expect(term("dag", { count: 1 })).toBe("automatización");
  });

  it("names data sources and says so when the id is missing", () => {
    expect(dataSourceName("sap_successfactors")).toBe("SAP SuccessFactors");
    expect(dataSourceName("new_source")).toBe("new source");
    expect(dataSourceName("")).toBe("Sin información");
  });

  it("detects technical words regardless of case, with plurals and identifiers", () => {
    expect(FORBIDDEN_UI_TERMS).toContain("Cartucho");
    expect(forbiddenTermsIn("Cartuchos conectados")).toEqual(["Cartucho"]);
    expect(forbiddenTermsIn("cartridge_id: acme")).toEqual(["cartridge"]);
    expect(forbiddenTermsIn("Expresión cron")).toEqual(["Cron"]);
    expect(forbiddenTermsIn("slug pendiente")).toEqual(["Slug"]);
    expect(forbiddenTermsIn("Sincronizado · Cronograma")).toEqual([]);
    expect(forbiddenTermsIn("Fuentes de datos conectadas")).toEqual([]);
  });

  it("rejects machine statuses rendered as literals", () => {
    expect(forbiddenTermsIn("estado dry_run_passed")).toEqual(["dry_run_passed"]);
    expect(forbiddenTermsIn("blocked_by_sap")).toEqual(["blocked_by_sap"]);
    expect(forbiddenTermsIn("12 afectados · recommendation_only")).toEqual(["recommendation_only"]);
    expect(forbiddenTermsIn("Simulado con éxito · Solo recomendación")).toEqual([]);
  });
});
