import type { SapB1CatalogEntry, SapB1Parameter } from "./types";

export const PARAMETER_KINDS = ["account", "threshold", "setting", "branch"] as const;
export const ACCOUNT_KEYS = ["revenue", "cogs"] as const;
export type AccountKey = (typeof ACCOUNT_KEYS)[number];
export const ANY = "*";

const ALIAS_RE = /^[a-z][a-z0-9_]{0,31}$/;
const PERIOD_RE = /^\d{4}-(0[1-9]|1[0-2])$/;
const KEY_RE = /^[a-z][a-z0-9_]{0,63}$/;
const ACCOUNT_RE = /^[A-Za-z0-9.-]{1,15}\*?$/;
const WAREHOUSE_RE = /^[A-Za-z0-9._-]{1,8}$/;
const NUMBER_RE = /^[+-]?(\d[\d_]*)?(\.\d+)?([eE][+-]?\d+)?$/;

export interface BranchRow {
  company: string;
  warehouse: string;
  name: string;
}

export interface AccountRow {
  company: string;
  key: AccountKey;
  codes: string;
}

export interface ParametersForm {
  values: Record<string, string>;
  branches: BranchRow[];
  accounts: AccountRow[];
  extra: SapB1Parameter[];
}

export interface ParsedParameters {
  parameters: SapB1Parameter[];
  errors: string[];
}

function isNumber(value: string): boolean {
  const trimmed = value.trim();
  return trimmed !== "" && /\d/.test(trimmed) && NUMBER_RE.test(trimmed);
}

export function entryText(parameter: SapB1Parameter): string {
  return `${parameter.kind}:${parameter.company}:${parameter.period}:${parameter.key}=${parameter.value}`;
}

export function validateParameter(parameter: SapB1Parameter, catalog: SapB1CatalogEntry[] = []): string | null {
  const { kind, company, period, key } = parameter;
  const value = parameter.value.trim();
  if (!(PARAMETER_KINDS as readonly string[]).includes(kind)) return `Tipo desconocido «${kind}».`;
  if (!value) return `«${key || kind}» necesita un valor.`;
  if (/[;\n\r]/.test(value)) return `El valor de «${key}» no puede llevar punto y coma ni saltos de línea.`;
  if (company !== ANY && !ALIAS_RE.test(company)) return `Empresa «${company}» inválida: usa el alias en minúsculas o *.`;
  if (period !== ANY && !PERIOD_RE.test(period)) return `Periodo «${period}» inválido: usa AAAA-MM o *.`;
  if (kind === "branch") {
    if (company === ANY) return "Cada filial necesita la empresa a la que pertenece.";
    if (period !== ANY) return "Las filiales no llevan periodo.";
    if (!WAREHOUSE_RE.test(key)) return `Código de almacén «${key}» inválido (hasta 8 letras, números, punto, guion o guion bajo).`;
    if (value.length > 100) return `El nombre de la filial «${key}» supera 100 caracteres.`;
    return null;
  }
  if (!KEY_RE.test(key)) return `Clave «${key}» inválida.`;
  if (kind === "account") {
    if (period !== ANY) return "Las cuentas no llevan periodo.";
    if (!(ACCOUNT_KEYS as readonly string[]).includes(key)) return "Las cuentas son de ingresos (revenue) o de costo de ventas (cogs).";
    const codes = value.split(",").map((code) => code.trim());
    if (!codes.every((code) => ACCOUNT_RE.test(code))) {
      return `Cuentas «${value}» inválidas: códigos separados por coma; un * al final toma el prefijo.`;
    }
    return null;
  }
  const spec = catalog.find((item) => item.key === key);
  if (kind === "threshold" && !isNumber(value)) return `El umbral «${key}» necesita un número.`;
  if (spec) {
    if (spec.kind !== kind) return `«${key}» es de tipo ${spec.kind}, no ${kind}.`;
    if (spec.unit !== "texto" && !isNumber(value)) return `«${key}» necesita un número.`;
  }
  return null;
}

export function parseParametersText(text: string, catalog: SapB1CatalogEntry[] = []): ParsedParameters {
  const parameters: SapB1Parameter[] = [];
  const errors: string[] = [];
  const seen = new Set<string>();
  for (const raw of (text || "").split(/[;\n]/)) {
    const chunk = raw.trim();
    if (!chunk || chunk.startsWith("#")) continue;
    const separator = chunk.indexOf("=");
    const parts = (separator >= 0 ? chunk.slice(0, separator) : chunk).split(":").map((part) => part.trim());
    const value = separator >= 0 ? chunk.slice(separator + 1).trim() : "";
    if (separator < 0 || parts.length !== 4 || !value) {
      errors.push(`«${chunk}» debe verse como tipo:empresa:periodo:clave=valor.`);
      continue;
    }
    const [kind, company, period, key] = parts;
    const normalized = kind === "account" ? value.split(",").map((code) => code.trim()).join(",") : value;
    const parameter: SapB1Parameter = { kind, company, period, key, value: normalized };
    const error = validateParameter(parameter, catalog);
    if (error) {
      errors.push(error);
      continue;
    }
    const identity = `${kind}:${company}:${period}:${key}`;
    if (seen.has(identity)) {
      errors.push(`Entrada repetida ${identity}.`);
      continue;
    }
    seen.add(identity);
    parameters.push(parameter);
  }
  return { parameters, errors };
}

export function emptyForm(catalog: SapB1CatalogEntry[]): ParametersForm {
  return { values: Object.fromEntries(catalog.map((item) => [item.key, ""])), branches: [], accounts: [], extra: [] };
}

export function formFromParameters(parameters: SapB1Parameter[], catalog: SapB1CatalogEntry[]): ParametersForm {
  const form = emptyForm(catalog);
  const catalogKeys = new Set(catalog.map((item) => item.key));
  for (const parameter of parameters) {
    const general = parameter.company === ANY && parameter.period === ANY;
    if (general && (parameter.kind === "threshold" || parameter.kind === "setting") && catalogKeys.has(parameter.key)) {
      form.values[parameter.key] = parameter.value;
    } else if (parameter.kind === "branch" && parameter.period === ANY) {
      form.branches.push({ company: parameter.company, warehouse: parameter.key, name: parameter.value });
    } else if (parameter.kind === "account" && parameter.period === ANY && (ACCOUNT_KEYS as readonly string[]).includes(parameter.key)) {
      form.accounts.push({ company: parameter.company, key: parameter.key as AccountKey, codes: parameter.value });
    } else {
      form.extra.push(parameter);
    }
  }
  return form;
}

export function formParameters(form: ParametersForm, catalog: SapB1CatalogEntry[]): SapB1Parameter[] {
  const entries: SapB1Parameter[] = [];
  for (const item of catalog) {
    const value = (form.values[item.key] ?? "").trim();
    if (value) entries.push({ kind: item.kind, company: ANY, period: ANY, key: item.key, value });
  }
  for (const account of form.accounts) {
    entries.push({
      kind: "account",
      company: account.company.trim() || ANY,
      period: ANY,
      key: account.key,
      value: account.codes.split(",").map((code) => code.trim()).filter(Boolean).join(","),
    });
  }
  for (const branch of form.branches) {
    entries.push({ kind: "branch", company: branch.company.trim(), period: ANY, key: branch.warehouse.trim(), value: branch.name.trim() });
  }
  entries.push(...form.extra);
  return entries;
}

export function validateForm(form: ParametersForm, catalog: SapB1CatalogEntry[]): string[] {
  const errors: string[] = [];
  const seen = new Set<string>();
  for (const parameter of formParameters(form, catalog)) {
    const error = validateParameter(parameter, catalog);
    if (error) errors.push(error);
    const identity = `${parameter.kind}:${parameter.company}:${parameter.period}:${parameter.key}`;
    if (seen.has(identity)) errors.push(`Entrada repetida ${identity}.`);
    seen.add(identity);
  }
  return errors;
}

export function buildParametersText(form: ParametersForm, catalog: SapB1CatalogEntry[]): string {
  const lines = formParameters(form, catalog).map(entryText);
  return lines.length ? `${lines.join("\n")}\n` : "";
}
