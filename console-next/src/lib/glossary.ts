export interface GlossaryNoun {
  one: string;
  other: string;
}

export const GLOSSARY = {
  cartridge: { one: "Fuente de datos", other: "Fuentes de datos" },
  dataset: { one: "dataset", other: "datasets" },
  dag: { one: "automatización", other: "automatizaciones" },
  schedule: { one: "Frecuencia", other: "Frecuencias" },
  tools: { one: "Herramienta", other: "Herramientas" },
  knowledge: { one: "Conocimiento", other: "Conocimiento" },
  scope: { one: "Alcance", other: "Alcances" },
  technicalSql: { one: "Consulta técnica", other: "Consultas técnicas" },
  extraFields: { one: "Campo adicional", other: "Campos adicionales" },
  payload: { one: "contenido", other: "contenidos" },
  contextData: { one: "Datos de contexto", other: "Datos de contexto" },
} as const satisfies Record<string, GlossaryNoun>;

export type GlossaryKey = keyof typeof GLOSSARY;

export const LAYER_LABELS = {
  bronze: "Tablas de Origen (Bronce)",
  silver: "Modelado y Limpieza (Plata)",
  gold: "Indicadores y KPIs (Oro)",
} as const;

export function term(key: GlossaryKey, opts: { count?: number; lower?: boolean } = {}): string {
  const noun: GlossaryNoun = GLOSSARY[key];
  const value = opts.count !== undefined && opts.count !== 1 ? noun.other : noun.one;
  return opts.lower ? value.toLocaleLowerCase("es") : value;
}

const DATA_SOURCE_NAMES: Record<string, string> = {
  banxico: "Banxico SIE",
  hubspot: "HubSpot CRM",
  inegi: "INEGI",
  platform: "Plataforma",
  replicon: "Replicon",
  salesforce: "Salesforce",
  sap_b1: "SAP Business One",
  sap_hcm: "SAP HCM",
  sap_s4hana: "SAP S/4HANA",
  sap_successfactors: "SAP SuccessFactors",
  sec_edgar: "SEC EDGAR",
};

export function dataSourceName(id: string | null | undefined): string {
  const key = (id ?? "").trim();
  if (!key) return "Sin información";
  return DATA_SOURCE_NAMES[key] ?? key.replace(/_/g, " ");
}

export const FORBIDDEN_UI_TERMS = [
  "payload",
  "extraJson",
  "Slug",
  "Max tokens",
  "Temperatura",
  "Cron",
  "cartridge",
  "Cartucho",
  "VARCHAR",
  "BIGINT",
  "read_parquet",
  "join_hint",
  "many_to_one",
] as const;

const FORBIDDEN_PATTERNS = FORBIDDEN_UI_TERMS.map((word) => ({
  word,
  pattern: new RegExp(`(?<!\\p{L})${word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?:e?s)?(?!\\p{L})`, "iu"),
}));

export function forbiddenTermsIn(text: string): string[] {
  return FORBIDDEN_PATTERNS.filter(({ pattern }) => pattern.test(text)).map(({ word }) => word);
}
