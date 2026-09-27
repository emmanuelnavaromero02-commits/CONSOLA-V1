import type { CatalogColumn, SemanticType } from "@/lib/data/types";

// Mirror of refinement/app/catalog_copilot_rules.semantic_type. Both are
// tested against contracts/fixtures/catalog-human-types-v1.json.
export const HUMAN_TYPE_LABEL: Record<SemanticType, string> = {
  identifier: "Identificador",
  text: "Texto",
  date: "Fecha",
  datetime: "Fecha y hora",
  time: "Hora",
  money: "Monto Monetario",
  number: "Número",
  integer: "Número entero",
  percent: "Porcentaje",
  boolean: "Sí/No",
  complex: "Lista o estructura",
};

export const SEMANTIC_TYPES = Object.keys(HUMAN_TYPE_LABEL) as SemanticType[];

const KEY_TOKENS = new Set(["id", "code", "codigo", "key", "externalcode", "pernr", "uuid", "guid"]);
const KEY_NAMES = new Set(["pernr", "bukrs", "kostl", "orgeh", "kunnr", "lifnr", "idempleado", "employeenumber"]);
const STRONG_MONEY = new Set([
  "amount", "monto", "importe", "price", "precio", "cost", "costo", "revenue", "ingreso", "ingresos",
  "betrg", "dmbtr", "netpr", "mrr", "arr", "billing", "facturacion", "margin", "margen", "budget",
  "presupuesto", "salary", "salario", "sueldo", "paycomp",
]);
const WEAK_MONEY = new Set(["total", "value", "valor", "pay", "comp"]);
const COUNT_TOKENS = new Set([
  "count", "qty", "quantity", "headcount", "cantidad", "num", "numero", "employees", "empleados",
  "conteo", "days", "dias", "hours", "horas",
]);
const PERCENT_TOKENS = new Set(["pct", "percent", "percentage", "ratio", "rate", "porcentaje", "tasa"]);
const INTEGER_BASES = new Set([
  "TINYINT", "SMALLINT", "INTEGER", "INT", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER",
  "UBIGINT", "UHUGEINT", "INT1", "INT2", "INT4", "INT8", "SERIAL", "BIGSERIAL", "SMALLSERIAL",
]);
const DECIMAL_BASES = new Set([
  "FLOAT", "FLOAT4", "FLOAT8", "DOUBLE", "DOUBLE PRECISION", "REAL", "DECIMAL", "NUMERIC", "MONEY",
]);
const TEXT_BASES = new Set(["VARCHAR", "TEXT", "STRING", "CHAR", "BPCHAR", "UUID", "NAME"]);

export function splitTokens(name: string): string[] {
  let text = String(name ?? "").replace(/([a-z0-9])([A-Z])/g, "$1 $2");
  text = text.replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2");
  return text.toLowerCase().split(/[^A-Za-z0-9]+/).filter(Boolean);
}

export function normalizeName(name: string): string {
  return Array.from(String(name ?? "").toLowerCase())
    .filter((char) => /[\p{L}\p{N}]/u.test(char))
    .join("");
}

function rawType(value: string | null | undefined): string {
  return String(value ?? "").trim().toUpperCase().replace(/\s+/g, " ");
}

function baseOf(raw: string): string {
  return raw.split("(")[0].trim();
}

function isComplex(raw: string): boolean {
  return (
    raw.endsWith("]")
    || ["LIST", "STRUCT", "MAP", "UNION", "ARRAY"].some((prefix) => raw.startsWith(prefix))
    || raw === "JSON"
    || raw === "JSONB"
  );
}

function isInteger(raw: string): boolean {
  if (/^(DECIMAL|NUMERIC)\s*\(\s*\d+\s*,\s*0\s*\)$/.test(raw)) return true;
  return INTEGER_BASES.has(baseOf(raw));
}

function isNumeric(raw: string): boolean {
  return isInteger(raw) || DECIMAL_BASES.has(baseOf(raw));
}

function isTexty(raw: string): boolean {
  const base = baseOf(raw);
  return TEXT_BASES.has(base) || base.startsWith("CHARACTER") || base.startsWith("VARCHAR") || !base;
}

function keyLike(tokens: string[], normalized: string): boolean {
  return KEY_TOKENS.has(normalized) || KEY_NAMES.has(normalized) || tokens.some((token) => KEY_TOKENS.has(token));
}

export function semanticTypeFromRaw(
  rawTypeValue: string | null | undefined,
  column: string,
  isKey = false,
): SemanticType {
  const raw = rawType(rawTypeValue);
  const tokens = splitTokens(column);
  const normalized = normalizeName(column);
  const tokenSet = new Set([...tokens, normalized]);
  const hits = (set: Set<string>) => [...tokenSet].some((token) => set.has(token));
  if (raw.startsWith("BOOL")) return "boolean";
  if (isComplex(raw)) return "complex";
  if (raw === "DATE") return "date";
  if (raw.startsWith("TIMESTAMP") || raw.startsWith("DATETIME")) return "datetime";
  if (raw.startsWith("TIME")) return "time";
  const key = isKey || keyLike(tokens, normalized);
  if (isNumeric(raw)) {
    if (key) return "identifier";
    const counts = hits(COUNT_TOKENS);
    const strongMoney = hits(STRONG_MONEY) || normalized.includes("paycomp");
    if (strongMoney && !counts) return "money";
    if (hits(PERCENT_TOKENS)) return "percent";
    if (hits(WEAK_MONEY) && !counts && !isInteger(raw)) return "money";
    return isInteger(raw) ? "integer" : "number";
  }
  if (isTexty(raw) && key) return "identifier";
  return "text";
}

export function isSemanticType(value: unknown): value is SemanticType {
  return typeof value === "string" && value in HUMAN_TYPE_LABEL;
}

export function columnSemanticType(column: CatalogColumn): SemanticType {
  if (isSemanticType(column.semantic_type)) return column.semantic_type;
  return semanticTypeFromRaw(column.type, column.name, Boolean(column.is_key));
}

export function humanTypeLabel(column: CatalogColumn): string {
  return HUMAN_TYPE_LABEL[columnSemanticType(column)];
}
