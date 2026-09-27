import type { CatalogClassification, CatalogColumn } from "@/lib/data/types";

export const CLASSIFICATION_LABEL: Record<CatalogClassification, string> = {
  pii: "Dato Personal Identificable",
  financial: "Información Financiera",
  confidential: "Confidencial",
};

const ORDER: CatalogClassification[] = ["pii", "financial", "confidential"];

const NAME_KIND: Record<string, string> = {
  email: "correo electrónico",
  phone: "teléfono",
  rfc: "RFC",
  curp: "CURP",
  nss: "número de seguridad social",
  passport: "pasaporte",
  national_id: "identificación oficial",
  birth: "fecha de nacimiento",
  address: "domicilio",
  gender: "género",
  marital: "estado civil",
  nationality: "nacionalidad",
  ip: "dirección IP",
  person_name: "nombre de persona",
  salary: "salario",
  compensation: "compensación",
  bonus: "bono",
  bank_account: "cuenta bancaria",
  account: "cuenta",
  card: "tarjeta",
  performance_rating: "calificación de desempeño",
  termination_reason: "motivo de baja",
  disciplinary: "información disciplinaria",
  disciplinaria: "información disciplinaria",
  disciplinario: "información disciplinaria",
  medical: "información médica",
  medico: "información médica",
  medica: "información médica",
};

const PATTERN_KIND: Record<string, string> = {
  rfc: "RFC",
  curp: "CURP",
  email: "correo electrónico",
  phone_mx: "teléfono de México",
  clabe: "CLABE interbancaria",
  card: "tarjeta bancaria",
  person_name: "nombre de persona",
};

const PROTECTION: Record<string, string> = {
  masked: "enmascarado",
  encrypted: "cifrado",
  shadowed: "oculto",
};

export function orderedClassifications(values?: readonly string[] | null): CatalogClassification[] {
  const wanted = new Set(values ?? []);
  return ORDER.filter((name) => wanted.has(name));
}

export function classificationLabel(value: CatalogClassification): string {
  return CLASSIFICATION_LABEL[value];
}

export function isSensitive(column: CatalogColumn): boolean {
  return orderedClassifications(column.classifications).some((name) => name === "pii" || name === "financial");
}

export function hasPersonalData(column: CatalogColumn): boolean {
  return orderedClassifications(column.classifications).includes("pii");
}

export function basisLabel(code: string): string {
  const [kind, ...rest] = String(code ?? "").split(":");
  const value = rest.join(":");
  switch (kind) {
    case "declared":
      return `Declarado por la fuente de datos (${PROTECTION[value] ?? "protegido"})`;
    case "name":
      if (value === "exact") return "Mismo nombre de columna en ambas tablas";
      if (value === "class") return "Nombres equivalentes de la misma llave de negocio";
      return `El nombre de la columna indica ${NAME_KIND[value] ?? "un dato sensible"}`;
    case "pattern":
      return `Los valores tienen formato de ${PATTERN_KIND[value] ?? "dato sensible"}`;
    case "type":
      return value === "money" ? "Columna de monto con nombre de salario o compensación" : "Tipo de columna";
    case "key":
      return value === "exact"
        ? "Llave única verificada con conteo exacto"
        : "Llave probable según el perfil estadístico";
    case "types":
      return "Tipos de datos compatibles";
    case "containment": {
      const ratio = Number(value);
      return Number.isFinite(ratio)
        ? `${Math.round(ratio * 100)}% de los valores existen en la tabla destino`
        : "Contención de valores verificada";
    }
    default:
      return "Regla determinista del Copiloto";
  }
}

export function confidenceLabel(value?: number | null): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return `Confianza ${Math.round(Math.min(1, Math.max(0, value)) * 100)}%`;
}
