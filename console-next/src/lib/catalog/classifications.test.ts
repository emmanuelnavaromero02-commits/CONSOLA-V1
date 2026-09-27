import { describe, expect, it } from "vitest";

import {
  CLASSIFICATION_LABEL,
  basisLabel,
  classificationLabel,
  confidenceLabel,
  hasPersonalData,
  isSensitive,
  orderedClassifications,
} from "./classifications";

describe("classifications", () => {
  it("uses the decided business labels", () => {
    expect(CLASSIFICATION_LABEL).toEqual({
      pii: "Dato Personal Identificable",
      financial: "Información Financiera",
      confidential: "Confidencial",
    });
    expect(classificationLabel("financial")).toBe("Información Financiera");
  });

  it("orders and filters classifications", () => {
    expect(orderedClassifications(["confidential", "bogus", "pii"])).toEqual(["pii", "confidential"]);
    expect(orderedClassifications(null)).toEqual([]);
  });

  it("detects personal and sensitive columns", () => {
    expect(hasPersonalData({ name: "email", classifications: ["pii", "confidential"] })).toBe(true);
    expect(hasPersonalData({ name: "salary", classifications: ["financial"] })).toBe(false);
    expect(isSensitive({ name: "salary", classifications: ["financial"] })).toBe(true);
    expect(isSensitive({ name: "notes", classifications: ["confidential"] })).toBe(false);
  });

  it("explains every rule code in Spanish", () => {
    expect(basisLabel("declared:masked")).toBe("Declarado por la fuente de datos (enmascarado)");
    expect(basisLabel("name:rfc")).toBe("El nombre de la columna indica RFC");
    expect(basisLabel("pattern:email")).toBe("Los valores tienen formato de correo electrónico");
    expect(basisLabel("type:money")).toBe("Columna de monto con nombre de salario o compensación");
    expect(basisLabel("key:exact")).toBe("Llave única verificada con conteo exacto");
    expect(basisLabel("key:approximate")).toBe("Llave probable según el perfil estadístico");
    expect(basisLabel("name:exact")).toBe("Mismo nombre de columna en ambas tablas");
    expect(basisLabel("containment:0.98")).toBe("98% de los valores existen en la tabla destino");
    expect(basisLabel("whatever")).toBe("Regla determinista del Copiloto");
  });

  it("formats confidence as a bounded percentage", () => {
    expect(confidenceLabel(0.97)).toBe("Confianza 97%");
    expect(confidenceLabel(3)).toBe("Confianza 100%");
    expect(confidenceLabel(null)).toBeNull();
  });
});
