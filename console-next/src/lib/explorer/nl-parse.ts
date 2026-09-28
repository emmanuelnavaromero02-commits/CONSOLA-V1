import { KIND_LABELS, OPERATOR_LABELS, operatorAllowed, type ColumnKind, type ExplorerOp } from "./operators";

// Deterministic Spanish parser: clauses it cannot map are reported in `unrecognized`, never guessed.

export interface NlColumn {
  name: string;
  kind: ColumnKind;
  labels?: string[];
}

export interface NlFilter {
  column: string;
  op: ExplorerOp;
  value: string;
  valueTo: string;
  values?: string[];
}

export interface NlSort {
  column: string;
  direction: "asc" | "desc";
}

export interface NlUnrecognized {
  text: string;
  hint: string;
}

export interface NlParseResult {
  filters: NlFilter[];
  sort: NlSort[];
  limit: number | null;
  latestOnly: boolean;
  unrecognized: NlUnrecognized[];
  notes: string[];
}

export interface NlOptions {
  today?: Date;
  latestAvailable?: boolean;
  allowLimit?: boolean;
}

type TokenType = "word" | "number" | "date" | "quoted" | "symbol" | "sep";

interface Token {
  raw: string;
  fold: string;
  start: number;
  end: number;
  type: TokenType;
}

interface Clause {
  tokens: Token[];
  prefix: string | null;
  first: boolean;
}

type OpMatch = { op: ExplorerOp | "period"; start: number; end: number };

class ParseIssue extends Error {
  constructor(public hint: string) {
    super(hint);
  }
}

const TOKEN_RE =
  /"([^"]*)"|“([^”]*)”|«([^»]*)»|'([^']*)'|(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}\/\d{1,2}\/\d{4}|\d{1,2}-\d{1,2}-\d{4})|(\$?-?\d+(?:[.,]\d+)*%?)|(>=|<=|<>|!=|=|>|<|:)|([,;])|([\p{L}\p{N}_@#]+(?:[.\-][\p{L}\p{N}_@#]+)*)/gu;

export function foldText(value: string): string {
  return value.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().trim();
}

function tokenize(text: string): Token[] {
  const tokens: Token[] = [];
  for (const match of text.matchAll(TOKEN_RE)) {
    const start = match.index ?? 0;
    const end = start + match[0].length;
    const quoted = match[1] ?? match[2] ?? match[3] ?? match[4];
    if (quoted !== undefined) {
      tokens.push({ raw: quoted, fold: foldText(quoted), start, end, type: "quoted" });
    } else if (match[5]) {
      tokens.push({ raw: match[5], fold: match[5], start, end, type: "date" });
    } else if (match[6]) {
      tokens.push({ raw: match[6], fold: match[6], start, end, type: "number" });
    } else if (match[7]) {
      tokens.push({ raw: match[7], fold: match[7], start, end, type: "symbol" });
    } else if (match[8]) {
      tokens.push({ raw: match[8], fold: match[8], start, end, type: "sep" });
    } else {
      tokens.push({ raw: match[9], fold: foldText(match[9]), start, end, type: "word" });
    }
  }
  return tokens;
}

const SPLIT_WORDS = new Set([
  "y", "e", "con", "sin", "donde", "que", "cuyo", "cuya", "cuyos", "cuyas", "pero", "ademas", "tambien",
]);
const SORT_WORDS = new Set([
  "ordenado", "ordenados", "ordenada", "ordenadas", "ordenar", "ordena", "ordenalos", "ordenalas", "orden",
]);
const KEEP_PREFIX = new Set(["con", "sin"]);
const BEFORE_QUE = new Set([
  "mayor", "menor", "mas", "menos", "igual", "mayores", "menores", "superior", "inferior",
]);
const BEFORE_CON = new Set([
  "empieza", "empiezan", "empiece", "empiecen", "comienza", "comienzan", "comience", "inicia", "inician", "inicie",
]);
const VERBS = new Set([
  "mostrar", "muestra", "muestrame", "muestren", "ver", "quiero", "queremos", "dame", "dime", "lista", "listar",
  "listame", "buscar", "busca", "buscame", "filtrar", "filtra", "filtrame", "trae", "traer", "traeme", "obtener",
  "obten", "consultar", "consulta", "ensename", "necesito", "selecciona", "seleccionar", "encuentra", "encontrar",
  "solo", "solamente", "unicamente", "todo", "todos", "todas", "tenga", "tengan", "tiene", "tienen",
]);
const ARTICLES = new Set(["el", "la", "los", "las", "lo", "un", "una", "unos", "unas", "su", "sus"]);
const STOPWORDS = new Set([
  ...ARTICLES, "de", "del", "al", "a", "en", "por", "the", "of", "columna", "campo", "valor", "que", "cuyo", "cuya",
  "cuyos", "cuyas", "tenga", "tengan", "tiene", "tienen",
]);
const RELATIVE_WORDS = new Set(["que", "cuyo", "cuya", "cuyos", "cuyas", "donde"]);
const TAIL_CONNECTORS = new Set(["de", "del", "en", "por", "para"]);
const CONTINUATION_WORDS = new Set(["luego", "despues", "tambien", "por", "segun", ...ARTICLES]);
const COPULAS = new Set(["es", "esta", "son", "estan", "sea", "sean", "fue", "fueron"]);
const FILLER_NOUNS = new Set(["registros", "registro", "filas", "fila", "resultados", "resultado", "datos", "elementos"]);
const SUBJECT_NOUNS = new Set([
  ...FILLER_NOUNS, "empleados", "empleadas", "trabajadores", "colaboradores", "personas", "clientes", "proveedores",
  "facturas", "pedidos", "ordenes", "productos", "articulos", "usuarios", "cuentas", "movimientos", "transacciones",
  "operaciones", "documentos", "casos", "candidatos", "vacantes", "puestos",
]);
const RESERVED_VALUE_WORDS = new Set([
  ...SORT_WORDS, "primeros", "primeras", "top", "limitar", "limite", "solo", "carga", "descendente", "ascendente",
  "desc", "asc", "alfabeticamente", "descendentemente", "ascendentemente",
]);
const RELATIVE_DATE_PAIRS: Array<[Set<string>, Set<string>]> = [
  [new Set(["este", "esta"]), new Set(["mes", "ano", "anio", "semana"])],
  [new Set(["ano", "anio", "mes"]), new Set(["pasado", "anterior", "actual"])],
  [new Set(["ultimo", "ultima"]), new Set(["mes", "ano", "anio", "semana"])],
];

const OPERATOR_PHRASES: Array<[string, ExplorerOp | "period"]> = [
  ["mayor o igual que", "gte"], ["mayor o igual a", "gte"], ["mayor o igual al", "gte"],
  ["igual o mayor que", "gte"], ["igual o mayor a", "gte"], ["al menos", "gte"], ["como minimo", "gte"],
  ["desde", "gte"], [">=", "gte"],
  ["menor o igual que", "lte"], ["menor o igual a", "lte"], ["menor o igual al", "lte"],
  ["igual o menor que", "lte"], ["igual o menor a", "lte"], ["como maximo", "lte"], ["a lo sumo", "lte"],
  ["hasta", "lte"], ["<=", "lte"],
  ["mayor que", "gt"], ["mayor a", "gt"], ["mayor al", "gt"], ["mayores que", "gt"], ["mayores a", "gt"],
  ["mas de", "gt"], ["mas del", "gt"], ["mas que", "gt"], ["superior a", "gt"], ["superior al", "gt"],
  ["superiores a", "gt"], ["por encima de", "gt"], ["por encima del", "gt"], ["despues de", "gt"],
  ["despues del", "gt"], ["posterior a", "gt"], ["posterior al", "gt"], ["arriba de", "gt"], [">", "gt"],
  ["menor que", "lt"], ["menor a", "lt"], ["menor al", "lt"], ["menores que", "lt"], ["menores a", "lt"],
  ["menos de", "lt"], ["menos del", "lt"], ["menos que", "lt"], ["inferior a", "lt"], ["inferior al", "lt"],
  ["inferiores a", "lt"], ["por debajo de", "lt"], ["por debajo del", "lt"], ["antes de", "lt"],
  ["antes del", "lt"], ["anterior a", "lt"], ["anterior al", "lt"], ["debajo de", "lt"], ["<", "lt"],
  ["entre", "between"],
  ["no contiene", "not_contains"], ["no contienen", "not_contains"], ["no contenga", "not_contains"],
  ["no incluye", "not_contains"], ["no incluyen", "not_contains"], ["no incluya", "not_contains"],
  ["sin la palabra", "not_contains"],
  ["contiene", "contains"], ["contienen", "contains"], ["contenga", "contains"], ["contengan", "contains"],
  ["incluye", "contains"], ["incluyen", "contains"], ["incluya", "contains"], ["incluyan", "contains"],
  ["con la palabra", "contains"], ["tenga la palabra", "contains"],
  ["empieza con", "starts_with"], ["empieza por", "starts_with"], ["empiezan con", "starts_with"],
  ["empiezan por", "starts_with"], ["empiece con", "starts_with"], ["empiece por", "starts_with"],
  ["comienza con", "starts_with"], ["comienza por", "starts_with"], ["comienzan con", "starts_with"],
  ["comience con", "starts_with"], ["inicia con", "starts_with"], ["inician con", "starts_with"],
  ["no esta vacio", "is_not_empty"], ["no esta vacia", "is_not_empty"], ["no vacio", "is_not_empty"],
  ["no vacia", "is_not_empty"], ["no esta en blanco", "is_not_empty"], ["tiene valor", "is_not_empty"],
  ["con valor", "is_not_empty"], ["con dato", "is_not_empty"], ["con datos", "is_not_empty"],
  ["esta vacio", "is_empty"], ["esta vacia", "is_empty"], ["vacio", "is_empty"], ["vacia", "is_empty"],
  ["vacios", "is_empty"], ["vacias", "is_empty"], ["en blanco", "is_empty"], ["sin valor", "is_empty"],
  ["sin dato", "is_empty"], ["sin datos", "is_empty"], ["es nulo", "is_empty"], ["nulo", "is_empty"],
  ["es uno de", "in"], ["es alguno de", "in"],
  ["no es", "neq"], ["no son", "neq"], ["no sea", "neq"], ["distinto de", "neq"], ["distinto a", "neq"],
  ["distinta de", "neq"], ["distinta a", "neq"], ["diferente de", "neq"], ["diferente a", "neq"],
  ["excepto", "neq"], ["salvo", "neq"], ["!=", "neq"], ["<>", "neq"],
  ["es igual a", "eq"], ["igual a", "eq"], ["igual al", "eq"], ["es", "eq"], ["son", "eq"], ["sea", "eq"],
  ["sean", "eq"], ["=", "eq"], [":", "eq"],
  ["en", "period"], ["durante", "period"], ["de", "period"], ["del", "period"],
];
const OPERATORS = OPERATOR_PHRASES.map(([phrase, op]) => ({ words: phrase.split(" "), op }));

const AGGREGATE_WORDS = new Set([
  "promedio", "media", "mediana", "maximo", "minimo", "suma", "total", "percentil", "desviacion", "moda",
  "doble", "triple", "mitad",
]);
const UNIT_WORDS = new Set([
  "anos", "ano", "anios", "anio", "meses", "mes", "dias", "dia", "semanas", "semana", "horas", "hora", "minutos",
  "pesos", "peso", "dolares", "dolar", "usd", "mxn", "eur", "euros", "unidades", "unidad", "piezas", "pieza",
]);
const MONTHS = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre",
  "diciembre",
];
const MONTH_ALIASES: Record<string, number> = Object.fromEntries([
  ...MONTHS.map((month, index) => [month, index] as const),
  ["setiembre", 8] as const,
]);
const NUMBER_WORDS: Record<string, number> = {
  un: 1, uno: 1, una: 1, dos: 2, tres: 3, cuatro: 4, cinco: 5, seis: 6, siete: 7, ocho: 8, nueve: 9, diez: 10,
  once: 11, doce: 12, trece: 13, catorce: 14, quince: 15, dieciseis: 16, diecisiete: 17, dieciocho: 18,
  diecinueve: 19, veinte: 20, treinta: 30, cuarenta: 40, cincuenta: 50, cien: 100, cientos: 100,
};
const TRUE_WORDS = new Set(["si", "verdadero", "true", "1", "yes", "cierto"]);
const FALSE_WORDS = new Set(["no", "falso", "false", "0"]);

const SYNONYM_GROUPS: string[][] = [
  ["salario", "sueldo", "salary", "wage", "pay", "remuneracion"],
  ["nombre", "name", "first", "firstname"],
  ["apellido", "apellidos", "surname", "lastname"],
  ["fecha", "fechas", "date", "dt"],
  ["ingreso", "contratacion", "alta", "hire", "hired", "hiring", "join", "joined", "joining"],
  ["ingresos", "revenue", "income"],
  ["inicio", "start", "begin", "comienzo"],
  ["fin", "end", "termino", "finalizacion"],
  ["nacimiento", "birth", "birthday", "dob"],
  ["creacion", "creado", "created", "create"],
  ["actualizacion", "actualizado", "updated", "modificacion", "modified", "modificado"],
  ["departamento", "departamentos", "depto", "department", "dept"],
  ["area", "areas"],
  ["ciudad", "ciudades", "city"],
  ["pais", "paises", "country"],
  ["estado", "estados", "status", "estatus", "state"],
  ["edad", "age"],
  ["genero", "sexo", "gender", "sex"],
  ["cliente", "clientes", "customer", "client"],
  ["proveedor", "proveedores", "supplier", "vendor"],
  ["monto", "montos", "importe", "importes", "amount"],
  ["cantidad", "cantidades", "quantity", "qty"],
  ["precio", "precios", "price"],
  ["correo", "correos", "email", "mail", "e-mail"],
  ["telefono", "telefonos", "phone"],
  ["empresa", "empresas", "compania", "company"],
  ["puesto", "puestos", "cargo", "cargos", "position", "title", "job"],
  ["antiguedad", "seniority", "tenure"],
  ["activo", "activos", "activa", "activas", "active", "enabled"],
  ["moneda", "monedas", "currency"],
  ["factura", "facturas", "invoice", "invoices"],
  ["pedido", "pedidos", "order", "orders"],
  ["descripcion", "description"],
  ["codigo", "code"],
  ["usuario", "usuarios", "user", "username"],
  ["hora", "horas", "time", "hour", "hours"],
  ["mes", "meses", "month"],
  ["ano", "anos", "anio", "anios", "year", "years"],
  ["dia", "dias", "day", "days"],
  ["tipo", "tipos", "type", "kind"],
  ["categoria", "categorias", "category"],
  ["region", "regiones"],
  ["sucursal", "sucursales", "branch"],
  ["almacen", "almacenes", "warehouse"],
  ["producto", "productos", "articulo", "articulos", "item", "items", "product"],
  ["saldo", "saldos", "balance"],
  ["costo", "costos", "cost"],
  ["venta", "ventas", "sale", "sales"],
  ["impuesto", "impuestos", "tax"],
  ["descuento", "descuentos", "discount"],
  ["pago", "pagos", "payment"],
  ["vencimiento", "due"],
  ["numero", "number", "num"],
  ["total", "totales"],
];
const CONCEPT = new Map<string, string>();
for (const group of SYNONYM_GROUPS) for (const word of group) CONCEPT.set(word, group[0]);

const COMPOUND_ALIASES: Record<string, string[]> = {
  first_name: ["nombre"],
  firstname: ["nombre"],
  last_name: ["apellido"],
  lastname: ["apellido"],
  hire_date: ["fecha de ingreso", "fecha de contratacion"],
  start_date: ["fecha de inicio"],
  end_date: ["fecha de fin"],
  birth_date: ["fecha de nacimiento"],
  date_of_birth: ["fecha de nacimiento"],
  created_at: ["fecha de creacion"],
  updated_at: ["fecha de actualizacion"],
};

function splitName(name: string): string[] {
  return foldText(name.replace(/([a-z0-9])([A-Z])/g, "$1 $2"))
    .split(/[^a-z0-9]+/)
    .filter(Boolean);
}

function singular(word: string): string {
  if (word.length > 4 && word.endsWith("es") && !/[aeiou]/.test(word[word.length - 3])) return word.slice(0, -2);
  if (word.length > 3 && word.endsWith("s") && /[aeiou]/.test(word[word.length - 2])) return word.slice(0, -1);
  return word;
}

function concept(word: string): string {
  const base = singular(word);
  return CONCEPT.get(word) ?? CONCEPT.get(base) ?? base;
}

function conceptKey(words: string[]): string[] {
  return [...new Set(words.filter((word) => !STOPWORDS.has(word)).map(concept))].sort();
}

interface ColumnIndex {
  column: NlColumn;
  exact: Set<string>;
  concepts: string[][];
}

function buildIndex(columns: NlColumn[]): ColumnIndex[] {
  return columns.map((column) => {
    const folded = foldText(column.name);
    const words = splitName(column.name);
    const labels = [...(column.labels ?? []), ...(COMPOUND_ALIASES[folded] ?? [])].map(foldText).filter(Boolean);
    const exact = new Set([folded, words.join(" "), words.join("_"), ...labels]);
    const concepts = [conceptKey(words), ...labels.map((label) => conceptKey(label.split(/\s+/)))].filter(
      (key) => key.length > 0,
    );
    return { column, exact, concepts };
  });
}

type Resolution = { column: NlColumn } | { ambiguous: string[] } | { missing: true };

function sameSet(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((item, index) => item === b[index]);
}

function resolveColumn(tokens: Token[], index: ColumnIndex[]): Resolution {
  const words = tokens.map((token) => token.fold).filter(Boolean);
  if (!words.length) return { missing: true };
  const joined = words.join(" ");
  const exact = index.filter((item) => item.exact.has(joined) || item.exact.has(words.join("_")));
  if (exact.length === 1) return { column: exact[0].column };
  const key = conceptKey(words);
  if (!key.length) return { missing: true };
  const equal = index.filter((item) => item.concepts.some((concepts) => sameSet(concepts, key)));
  if (equal.length === 1) return { column: equal[0].column };
  if (equal.length > 1) return { ambiguous: equal.map((item) => item.column.name) };
  const subset = index.filter((item) => item.concepts.some((concepts) => key.every((word) => concepts.includes(word))));
  if (subset.length === 1) return { column: subset[0].column };
  if (subset.length > 1) return { ambiguous: subset.map((item) => item.column.name) };
  return { missing: true };
}

function resolveColumnStrict(tokens: Token[], index: ColumnIndex[]): NlColumn | null {
  const words = tokens.map((token) => token.fold);
  const key = conceptKey(words);
  const matches = index.filter(
    (item) =>
      item.exact.has(words.join(" ")) ||
      item.exact.has(words.join("_")) ||
      (key.length > 0 && item.concepts.some((concepts) => sameSet(concepts, key))),
  );
  return matches.length ? matches[0].column : null;
}

function sortPhraseAt(tokens: Token[], at: number): string | null {
  for (const words of [...DESC_PHRASES, ...ASC_PHRASES]) {
    if (matchWords(tokens, at, words)) return sliceTokens(tokens.slice(at, at + words.length));
  }
  return null;
}

function relativeDateAt(tokens: Token[], at: number): string | null {
  const word = tokens[at]?.fold ?? "";
  const next = tokens[at + 1];
  if (word === "hoy" || word === "ayer") return tokens[at].raw;
  if (next && RELATIVE_DATE_PAIRS.some(([first, second]) => first.has(word) && second.has(next.fold))) {
    return sliceTokens(tokens.slice(at, at + 2));
  }
  if ((word === "ultimos" || word === "ultimas" || word === "hace") && next) {
    const amount = next.type === "number" || next.fold in NUMBER_WORDS;
    if (amount && unitSpan(tokens[at + 2]?.fold ?? "")) return sliceTokens(tokens.slice(at, at + 3));
  }
  const match = operatorAt(tokens, at);
  if (match?.op === "period" && next) {
    const value = tokens[match.end];
    if (value && ((value.type === "number" && /^\d{4}$/.test(value.raw)) || value.fold in MONTH_ALIASES || value.type === "date")) {
      return sliceTokens(tokens.slice(at, match.end + 1));
    }
  }
  return null;
}

function valueConflict(tokens: Token[], index: ColumnIndex[]): string | null {
  if (tokens.length < 2) return null;
  const temporal = index.some((item) => item.column.kind === "temporal");
  for (let at = 0; at < tokens.length; at += 1) {
    if (tokens[at].type === "quoted") continue;
    const match = operatorAt(tokens, at);
    if (match && match.op !== "period") return sliceTokens(tokens.slice(at, match.end));
    if (tokens[at].type === "word" && RESERVED_VALUE_WORDS.has(tokens[at].fold)) return tokens[at].raw;
    const sortPhrase = sortPhraseAt(tokens, at);
    if (sortPhrase) return sortPhrase;
    const relative = temporal ? relativeDateAt(tokens, at) : null;
    if (relative) return relative;
    for (const size of [1, 2]) {
      const window = tokens.slice(at, at + size);
      if (window.length === size && window.every((token) => token.type === "word") && resolveColumnStrict(window, index)) {
        return sliceTokens(window);
      }
    }
  }
  return null;
}

function columnOrThrow(tokens: Token[], index: ColumnIndex[], original: string): NlColumn {
  const resolved = resolveColumn(tokens, index);
  if ("column" in resolved) return resolved.column;
  const phrase = sliceOf(original, tokens) || "(sin columna)";
  if ("ambiguous" in resolved) {
    throw new ParseIssue(`«${phrase}» coincide con varias columnas: ${resolved.ambiguous.join(", ")}. Escribe el nombre exacto.`);
  }
  const available = index.slice(0, 8).map((item) => item.column.name).join(", ");
  throw new ParseIssue(`No encontré una columna para «${phrase}». Columnas disponibles: ${available}${index.length > 8 ? "…" : ""}.`);
}

function sliceOf(original: string, tokens: Token[]): string {
  if (!tokens.length) return "";
  return original.slice(tokens[0].start, tokens[tokens.length - 1].end).trim();
}

function splitClauses(tokens: Token[]): Clause[] {
  const clauses: Clause[] = [];
  let current: Token[] = [];
  let prefix: string | null = null;
  let betweenOpen = false;
  const flush = (nextPrefix: string | null) => {
    if (current.length || prefix) clauses.push({ tokens: current, prefix, first: clauses.length === 0 });
    current = [];
    prefix = nextPrefix;
    betweenOpen = false;
  };
  tokens.forEach((token, position) => {
    const previous = current[current.length - 1]?.fold;
    const next = tokens[position + 1]?.fold;
    const afterNext = tokens[position + 2]?.fold;
    if (token.type === "sep") {
      flush(null);
      return;
    }
    if (token.type === "word" && SORT_WORDS.has(token.fold) && (next === "por" || next === "segun")) {
      flush(null);
      current.push(token);
      return;
    }
    if (token.type === "word" && SPLIT_WORDS.has(token.fold)) {
      if ((token.fold === "y" || token.fold === "e") && betweenOpen) {
        current.push(token);
        betweenOpen = false;
        return;
      }
      if (token.fold === "que" && previous && BEFORE_QUE.has(previous)) {
        current.push(token);
        return;
      }
      if (RELATIVE_WORDS.has(token.fold) && current.length && operatorAt(tokens, position + 1)) {
        current.push(token);
        return;
      }
      if (token.fold === "con" && previous && BEFORE_CON.has(previous)) {
        current.push(token);
        return;
      }
      if ((token.fold === "con" || token.fold === "sin") && (
        next === "valor" || next === "dato" || next === "datos" || (next === "la" && afterNext === "palabra")
      )) {
        current.push(token);
        return;
      }
      flush(KEEP_PREFIX.has(token.fold) ? token.fold : null);
      return;
    }
    if (token.type === "word" && token.fold === "entre") betweenOpen = true;
    current.push(token);
  });
  flush(null);
  return clauses.filter((clause) => clause.tokens.length || clause.prefix);
}

function stripLeading(tokens: Token[], words: Set<string>): Token[] {
  let index = 0;
  while (index < tokens.length && tokens[index].type === "word" && words.has(tokens[index].fold)) index += 1;
  return tokens.slice(index);
}

function matchWords(tokens: Token[], at: number, words: string[]): boolean {
  return words.every((word, offset) => tokens[at + offset]?.fold === word && tokens[at + offset]?.type !== "quoted");
}

function operatorAt(tokens: Token[], at: number): OpMatch | null {
  let best: OpMatch | null = null;
  for (const { words, op } of OPERATORS) {
    if (matchWords(tokens, at, words) && (!best || at + words.length > best.end)) {
      best = { op, start: at, end: at + words.length };
    }
  }
  if (best && COPULAS.has(tokens[at].fold) && best.op === "eq" && best.end === at + 1) {
    const next = operatorAt(tokens, at + 1);
    if (next && next.op !== "eq" && next.op !== "period") return { op: next.op, start: at, end: next.end };
  }
  return best;
}

function operatorCandidates(tokens: Token[]): OpMatch[] {
  const found: OpMatch[] = [];
  for (let at = 0; at < tokens.length; at += 1) {
    const match = operatorAt(tokens, at);
    if (match) found.push(match);
  }
  return found;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function isoDate(date: Date): string {
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
}

function utcDate(year: number, month: number, day: number): Date | null {
  const date = new Date(Date.UTC(year, month, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month || date.getUTCDate() !== day) return null;
  return date;
}

function addDays(date: Date, days: number): Date {
  return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate() + days));
}

function addMonths(date: Date, months: number): Date {
  const target = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + months, 1));
  const lastDay = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  return new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth(), Math.min(date.getUTCDate(), lastDay)));
}

interface Period {
  from: Date;
  until: Date;
  single: boolean;
  label?: string;
}

function monthPeriod(year: number, month: number): Period {
  return { from: new Date(Date.UTC(year, month, 1)), until: new Date(Date.UTC(year, month + 1, 1)), single: false };
}

function parseNumberRaw(raw: string): string {
  let text = raw.trim().replace(/^\$/, "");
  if (text.endsWith("%")) {
    throw new ParseIssue(`No sé si «${raw}» significa ${text.slice(0, -1)} o ${Number(text.slice(0, -1)) / 100}; escribe el número tal como está en los datos.`);
  }
  const negative = text.startsWith("-");
  if (negative) text = text.slice(1);
  let normalized: string | null = null;
  if (/^\d+$/.test(text)) normalized = text;
  else if (/^\d+[.,]\d+$/.test(text)) {
    const [whole, fraction] = text.split(/[.,]/);
    if (fraction.length === 3) {
      throw new ParseIssue(
        `«${raw}» es ambiguo (¿miles o decimales?). Escríbelo sin separador de miles (p. ej. ${whole}${fraction}) o con el punto decimal explícito.`,
      );
    }
    normalized = `${whole}.${fraction}`;
  } else if (/^\d{1,3}(,\d{3}){2,}(\.\d+)?$/.test(text) || /^\d{1,3}(,\d{3})+\.\d+$/.test(text)) {
    normalized = text.replace(/,/g, "");
  } else if (/^\d{1,3}(\.\d{3}){2,}(,\d+)?$/.test(text) || /^\d{1,3}(\.\d{3})+,\d+$/.test(text)) {
    normalized = text.replace(/\./g, "").replace(",", ".");
  }
  if (normalized === null) throw new ParseIssue(`«${raw}» no es un número que pueda interpretar.`);
  return negative ? `-${normalized}` : normalized;
}

function multiply(value: string, factor: number): string {
  const result = Number(value) * factor;
  return Number.isInteger(result) ? String(result) : String(Number(result.toPrecision(15)));
}

interface ValueParse<T> {
  value: T;
  rest: Token[];
  note?: string;
}

function parseNumberValue(tokens: Token[]): ValueParse<string> {
  const [first, ...rest] = tokens;
  if (!first) throw new ParseIssue("Falta el número.");
  let value: string;
  let remaining = rest;
  if (first.type === "number") value = parseNumberRaw(first.raw);
  else if (first.type === "word" && first.fold in NUMBER_WORDS) value = String(NUMBER_WORDS[first.fold]);
  else if (first.type === "quoted") value = parseNumberRaw(first.raw);
  else throw new ParseIssue(`«${first.raw}» no es un número.`);
  const multiplier = remaining[0]?.fold;
  if (multiplier === "mil") {
    value = multiply(value, 1_000);
    remaining = remaining.slice(1);
  } else if (multiplier === "millon" || multiplier === "millones") {
    value = multiply(value, 1_000_000);
    remaining = remaining.slice(1);
  }
  let note: string | undefined;
  if (remaining[0] && UNIT_WORDS.has(remaining[0].fold)) {
    note = `«${first.raw} ${remaining[0].raw}» se tomó como el número ${value}.`;
    remaining = remaining.slice(1);
  }
  return { value, rest: remaining, note };
}

function unitSpan(unit: string): "day" | "month" | "year" | null {
  if (unit === "dia" || unit === "dias") return "day";
  if (unit === "mes" || unit === "meses") return "month";
  if (unit === "ano" || unit === "anos" || unit === "anio" || unit === "anios") return "year";
  return null;
}

function shift(date: Date, unit: "day" | "month" | "year", amount: number): Date {
  if (unit === "day") return addDays(date, amount);
  return addMonths(date, unit === "month" ? amount : amount * 12);
}

function parseDateValue(input: Token[], today: Date): ValueParse<Period> {
  const tokens = stripLeading(input, ARTICLES);
  const words = tokens.map((token) => token.fold);
  const first = tokens[0];
  if (!first) throw new ParseIssue("Falta la fecha.");
  const todayUtc = new Date(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()));
  const one = (date: Date, used: number, label?: string): ValueParse<Period> => ({
    value: { from: date, until: addDays(date, 1), single: true, label },
    rest: tokens.slice(used),
  });
  if (first.type === "date") {
    const iso = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(first.raw);
    const local = /^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$/.exec(first.raw);
    const parts = iso ? [Number(iso[1]), Number(iso[2]), Number(iso[3])] : local ? [Number(local[3]), Number(local[2]), Number(local[1])] : null;
    const date = parts ? utcDate(parts[0], parts[1] - 1, parts[2]) : null;
    if (!date) throw new ParseIssue(`«${first.raw}» no es una fecha válida (usa día/mes/año o AAAA-MM-DD).`);
    return one(date, 1);
  }
  if (first.type === "number" && /^\d{4}$/.test(first.raw)) {
    const year = Number(first.raw);
    return {
      value: { from: new Date(Date.UTC(year, 0, 1)), until: new Date(Date.UTC(year + 1, 0, 1)), single: false },
      rest: tokens.slice(1),
    };
  }
  if (first.fold in MONTH_ALIASES) {
    const month = MONTH_ALIASES[first.fold];
    const yearAt = words[1] === "de" || words[1] === "del" ? 2 : 1;
    const yearToken = tokens[yearAt];
    if (!yearToken || !/^\d{4}$/.test(yearToken.raw)) {
      throw new ParseIssue(`Indica el año del mes, p. ej. «${first.raw} de ${todayUtc.getUTCFullYear()}».`);
    }
    return { value: monthPeriod(Number(yearToken.raw), month), rest: tokens.slice(yearAt + 1) };
  }
  if (words[0] === "hoy") return one(todayUtc, 1);
  if (words[0] === "ayer") return one(addDays(todayUtc, -1), 1);
  const year = todayUtc.getUTCFullYear();
  const month = todayUtc.getUTCMonth();
  if ((words[0] === "este" || words[0] === "esta") && words[1] === "mes") {
    return { value: monthPeriod(year, month), rest: tokens.slice(2) };
  }
  if (words[0] === "mes" && words[1] === "actual") return { value: monthPeriod(year, month), rest: tokens.slice(2) };
  if ((words[0] === "ultimo" && words[1] === "mes") || ((words[0] === "mes" && (words[1] === "pasado" || words[1] === "anterior")))) {
    const previous = monthPeriod(year, month - 1);
    const label = `${MONTHS[previous.from.getUTCMonth()]} de ${previous.from.getUTCFullYear()}`;
    return {
      value: previous,
      rest: tokens.slice(2),
      note: `«${first.raw} ${tokens[1].raw}» se interpretó como el mes calendario anterior (${label}).`,
    };
  }
  if ((words[0] === "este" && (words[1] === "ano" || words[1] === "anio")) || (words[0] === "ano" && words[1] === "actual")) {
    return {
      value: { from: new Date(Date.UTC(year, 0, 1)), until: new Date(Date.UTC(year + 1, 0, 1)), single: false },
      rest: tokens.slice(2),
    };
  }
  if ((words[0] === "ano" || words[0] === "anio") && (words[1] === "pasado" || words[1] === "anterior")) {
    return {
      value: { from: new Date(Date.UTC(year - 1, 0, 1)), until: new Date(Date.UTC(year, 0, 1)), single: false },
      rest: tokens.slice(2),
    };
  }
  if ((words[0] === "ultimos" || words[0] === "ultimas") && tokens[1]) {
    const amount = tokens[1].type === "number" ? Number(tokens[1].raw) : NUMBER_WORDS[words[1]];
    const unit = unitSpan(words[2] ?? "");
    if (amount && Number.isInteger(amount) && unit) {
      return {
        value: { from: shift(todayUtc, unit, -amount), until: addDays(todayUtc, 1), single: false },
        rest: tokens.slice(3),
      };
    }
  }
  if (words[0] === "hace" && tokens[1]) {
    const amount = tokens[1].type === "number" ? Number(tokens[1].raw) : NUMBER_WORDS[words[1]];
    const unit = unitSpan(words[2] ?? "");
    if (amount && Number.isInteger(amount) && unit) return one(shift(todayUtc, unit, -amount), 3);
  }
  throw new ParseIssue(`«${sliceTokens(tokens)}» no es una fecha que pueda interpretar (usa día/mes/año, AAAA-MM-DD, un año o «este mes»).`);
}

function sliceTokens(tokens: Token[]): string {
  return tokens.map((token) => token.raw).join(" ");
}

function parseBoolean(tokens: Token[]): ValueParse<string> {
  const [first, ...rest] = tokens;
  const word = first?.fold ?? "";
  if (TRUE_WORDS.has(word)) return { value: "true", rest };
  if (FALSE_WORDS.has(word)) return { value: "false", rest };
  throw new ParseIssue(`«${first?.raw ?? ""}» no es un valor sí/no.`);
}

function textValue(tokens: Token[], original: string): string {
  if (tokens.length === 1 && tokens[0].type === "quoted") return tokens[0].raw;
  return sliceOf(original, tokens);
}

function aggregateHint(tokens: Token[], column: NlColumn): string | null {
  const words = tokens.filter((token) => token.type === "word").map((token) => token.fold);
  const aggregate = words.find((word) => AGGREGATE_WORDS.has(word));
  if (!aggregate || tokens.some((token) => token.type === "number" || token.type === "date")) return null;
  return (
    `Comparar contra ${aggregate === "promedio" || aggregate === "media" ? "el promedio" : `«${aggregate}»`} requiere calcularlo antes; ` +
    `todavía no se interpreta automáticamente. Calcula el valor y escríbelo como número (p. ej. «${column.name} mayor que 25000») ` +
    "o usa la consulta SQL técnica."
  );
}

interface Interpreted {
  filters: NlFilter[];
  notes: string[];
}

function filter(column: NlColumn, op: ExplorerOp, value = "", valueTo = ""): NlFilter {
  return { column: column.name, op, value, valueTo };
}

function requireOperator(op: ExplorerOp, column: NlColumn): void {
  if (!operatorAllowed(op, column.kind)) {
    throw new ParseIssue(
      `«${OPERATOR_LABELS[op]}» no aplica a ${column.name} (${KIND_LABELS[column.kind]}).`,
    );
  }
}

function periodFilters(column: NlColumn, op: ExplorerOp | "period", period: Period, periodTo?: Period): NlFilter[] {
  const from = isoDate(period.from);
  const until = isoDate(period.until);
  switch (op) {
    case "eq":
    case "period":
      return [filter(column, "gte", from), filter(column, "lt", until)];
    case "gt":
      return [filter(column, "gte", until)];
    case "gte":
      return [filter(column, "gte", from)];
    case "lt":
      return [filter(column, "lt", from)];
    case "lte":
      return [filter(column, "lt", until)];
    case "between":
      return [filter(column, "gte", from), filter(column, "lt", isoDate((periodTo ?? period).until))];
    case "neq":
      throw new ParseIssue(`Excluir un periodo de ${column.name} no está soportado; usa «antes de» o «después de».`);
    default:
      throw new ParseIssue(`«${OPERATOR_LABELS[op]}» no aplica a ${column.name} (fecha).`);
  }
}

function splitOnWord(tokens: Token[], word: string): Token[][] {
  const parts: Token[][] = [[]];
  for (const token of tokens) {
    if (token.type === "word" && token.fold === word) parts.push([]);
    else parts[parts.length - 1].push(token);
  }
  return parts;
}

function trailingColumn(rest: Token[], index: ColumnIndex[], original: string): NlColumn | null {
  const tail = stripLeading(rest, new Set(["de", "del", "en", "por", "para", ...ARTICLES]));
  if (!tail.length) return null;
  return columnOrThrow(tail, index, original);
}

function ensureConsumed(rest: Token[], original: string): void {
  if (rest.length) throw new ParseIssue(`Sobra «${sliceOf(original, rest)}» después del valor.`);
}

function interpretComparison(
  match: OpMatch,
  tokens: Token[],
  index: ColumnIndex[],
  original: string,
  today: Date,
): Interpreted {
  const left = stripLeading(tokens.slice(0, match.start), new Set([...ARTICLES, ...COPULAS]));
  const right = tokens.slice(match.end);
  const notes: string[] = [];
  let column: NlColumn | null = left.length ? columnOrThrow(left, index, original) : null;

  if (match.op === "is_empty" || match.op === "is_not_empty") {
    const target = column ?? trailingColumn(right, index, original);
    if (!target) throw new ParseIssue("Indica la columna, p. ej. «correo vacío».");
    if (column) ensureConsumed(right, original);
    return { filters: [filter(target, match.op)], notes };
  }
  if (!right.length) throw new ParseIssue("Falta el valor de la condición.");

  const resolveTail = (rest: Token[]): NlColumn => {
    if (column) {
      ensureConsumed(rest, original);
      return column;
    }
    const tail = trailingColumn(rest, index, original);
    if (!tail) throw missingColumnIssue(tokens, index, original);
    column = tail;
    return tail;
  };

  const probe = column ?? peekTailColumn(right, index);
  if (probe && (probe.kind === "number" || probe.kind === "temporal")) {
    const hint = aggregateHint(right, probe);
    if (hint) throw new ParseIssue(hint);
  }

  if (match.op === "between") {
    const byY = splitOnWord(right, "y");
    const parts = byY.length === 2 ? byY : splitOnWord(right, "e");
    if (parts.length !== 2 || !parts[0].length || !parts[1].length) {
      throw new ParseIssue("«entre» necesita dos valores: «entre 1000 y 5000».");
    }
    const kindColumn = column ?? peekTailColumn(parts[1], index);
    if (!kindColumn) throw missingColumnIssue(tokens, index, original);
    if (kindColumn.kind === "temporal") {
      const low = parseDateValue(parts[0], today);
      ensureConsumed(low.rest, original);
      const high = parseDateValue(parts[1], today);
      const target = resolveTail(high.rest);
      requireOperator("between", target);
      return { filters: periodFilters(target, "between", low.value, high.value), notes };
    }
    const low = parseNumberValue(parts[0]);
    ensureConsumed(low.rest, original);
    const high = parseNumberValue(parts[1]);
    const target = resolveTail(high.rest);
    requireOperator("between", target);
    if (Number(low.value) > Number(high.value)) {
      throw new ParseIssue("En «entre» el primer valor debe ser menor o igual que el segundo.");
    }
    for (const note of [low.note, high.note]) if (note) notes.push(note);
    return { filters: [filter(target, "between", low.value, high.value)], notes };
  }

  const kindColumn = column ?? peekTailColumn(right, index);
  if (!kindColumn) throw missingColumnIssue(tokens, index, original);

  if (match.op === "period" && kindColumn.kind !== "temporal") {
    throw new ParseIssue(`No entendí la condición sobre ${kindColumn.name}.`);
  }

  if (kindColumn.kind === "temporal" && match.op === "in") {
    throw new ParseIssue(`Para fechas usa «entre», «antes de», «después de» o «en <año>» con ${kindColumn.name}.`);
  }
  if (kindColumn.kind === "temporal" && match.op !== "contains" && match.op !== "not_contains" && match.op !== "starts_with") {
    const parsed = parseDateValue(right, today);
    const target = resolveTail(parsed.rest);
    if (parsed.note) notes.push(parsed.note);
    return { filters: periodFilters(target, match.op, parsed.value), notes };
  }

  if (match.op === "period") throw new ParseIssue(`No entendí la condición sobre ${kindColumn.name}.`);
  const op = match.op;

  if (kindColumn.kind === "number") {
    const alternatives = splitOnWord(right, "o");
    if (alternatives.length > 1 && (op === "eq" || op === "in")) {
      const values = alternatives.map((part) => {
        const parsed = parseNumberValue(part);
        ensureConsumed(parsed.rest, original);
        return parsed.value;
      });
      requireOperator("in", kindColumn);
      return { filters: [filter(resolveTail([]), "in", values.join(", "))], notes };
    }
    const parsed = parseNumberValue(right);
    const target = resolveTail(parsed.rest);
    requireOperator(op, target);
    if (parsed.note) notes.push(parsed.note);
    return { filters: [filter(target, op, parsed.value)], notes };
  }

  if (kindColumn.kind === "boolean") {
    const target = column ?? kindColumn;
    requireOperator(op, target);
    const parsed = parseBoolean(right);
    resolveTail(parsed.rest);
    return { filters: [filter(target, op, parsed.value)], notes };
  }

  const target = column;
  if (!target) {
    throw new ParseIssue(`Escribe la columna antes de la condición, p. ej. «nombre ${OPERATOR_LABELS[op]} …».`);
  }
  requireOperator(op, target);
  const alternatives = splitOnWord(right, "o").filter((part) => part.length);
  for (const part of alternatives) {
    const conflict = valueConflict(part, index);
    if (conflict) {
      const value = sliceOf(original, part);
      throw new ParseIssue(
        `El valor «${value}» incluye «${conflict}», que parece otra columna o condición. ` +
          `Si es parte del valor escríbelo entre comillas (p. ej. «${target.name} ${OPERATOR_LABELS[op]} "${value}"»); ` +
          "si es otra condición, sepárala con «y».",
      );
    }
  }
  if (alternatives.length > 1 && (op === "eq" || op === "in")) {
    const values = alternatives.map((part) => textValue(part, original));
    return { filters: [filter(target, "in", values.join(", "))], notes };
  }
  if (alternatives.length > 1 && op === "neq") {
    throw new ParseIssue("Excluir varios valores a la vez no está soportado; agrega una condición por valor.");
  }
  return { filters: [filter(target, op, textValue(right, original))], notes };
}

function peekTailColumn(tokens: Token[], index: ColumnIndex[]): NlColumn | null {
  for (let start = 1; start < tokens.length - 1; start += 1) {
    if (tokens[start].type !== "word" || !TAIL_CONNECTORS.has(tokens[start].fold)) continue;
    const tail = stripLeading(tokens.slice(start + 1), ARTICLES);
    if (!tail.length) continue;
    const resolved = resolveColumn(tail, index);
    if ("column" in resolved) return resolved.column;
  }
  return null;
}

function missingColumnIssue(tokens: Token[], index: ColumnIndex[], original: string): ParseIssue {
  let tail: Token[] = [];
  for (let start = tokens.length - 2; start >= 1; start -= 1) {
    if (tokens[start].type === "word" && TAIL_CONNECTORS.has(tokens[start].fold)) {
      tail = stripLeading(tokens.slice(start + 1), ARTICLES);
      break;
    }
  }
  const dates = index.filter((item) => item.column.kind === "temporal").map((item) => item.column.name);
  const timeUnit = tokens.some((token) => unitSpan(token.fold) !== null);
  const dateHint = timeUnit && dates.length
    ? ` Si quieres usar una fecha, escribe p. ej. «${dates[0]} antes de hace 3 años».`
    : "";
  if (tail.length) {
    try {
      columnOrThrow(tail, index, original);
    } catch (error) {
      if (error instanceof ParseIssue) return new ParseIssue(`${error.hint}${dateHint}`);
      throw error;
    }
  }
  return new ParseIssue(
    `¿A qué columna se refiere «${sliceOf(original, tokens)}»? Escribe, p. ej., «salario mayor que 1000».${dateHint}`,
  );
}

const DESC_PHRASES = [
  "de mayor a menor", "mayor a menor", "descendente", "descendentemente", "desc", "de la z a la a", "de z a a",
  "mas reciente primero", "mas recientes primero", "recientes primero", "mas nuevo primero", "mas nuevos primero",
  "de mas reciente a mas antiguo",
].map((phrase) => phrase.split(" "));
const ASC_PHRASES = [
  "de menor a mayor", "menor a mayor", "ascendente", "ascendentemente", "asc", "de la a a la z", "de a a z",
  "alfabeticamente", "mas antiguo primero", "mas antiguos primero", "antiguos primero", "de mas antiguo a mas reciente",
].map((phrase) => phrase.split(" "));

function directionSuffix(tokens: Token[]): { direction: "asc" | "desc" | null; body: Token[] } {
  for (const [direction, phrases] of [["desc", DESC_PHRASES], ["asc", ASC_PHRASES]] as const) {
    for (const words of phrases) {
      const at = tokens.length - words.length;
      if (at >= 0 && matchWords(tokens, at, words)) return { direction, body: tokens.slice(0, at) };
    }
  }
  return { direction: null, body: tokens };
}

function interpretSort(tokens: Token[], index: ColumnIndex[], original: string): NlSort {
  const body = stripLeading(tokens, new Set([...SORT_WORDS, "por", "segun", ...ARTICLES]));
  const { direction, body: columnTokens } = directionSuffix(body);
  if (!columnTokens.length) throw new ParseIssue("Indica la columna para ordenar: «ordenado por salario de mayor a menor».");
  const column = columnOrThrow(columnTokens, index, original);
  if (column.kind === "other") throw new ParseIssue(`No se puede ordenar por ${column.name} (estructura).`);
  return { column: column.name, direction: direction ?? "asc" };
}

function integerToken(token: Token | undefined): number | null {
  if (!token) return null;
  if (token.type === "number" && /^\d+$/.test(token.raw)) return Number(token.raw);
  if (token.type === "word" && token.fold in NUMBER_WORDS) return NUMBER_WORDS[token.fold];
  return null;
}

const TOP_DESC = [["mayores"], ["mas", "altos"], ["mas", "altas"], ["mas", "grandes"], ["mas", "caros"], ["mas", "caras"]];
const TOP_ASC = [["menores"], ["mas", "bajos"], ["mas", "bajas"], ["mas", "pequenos"], ["mas", "pequenas"], ["mas", "baratos"], ["mas", "baratas"]];
const TOP_RECENT = [["mas", "recientes"], ["ultimos"], ["ultimas"], ["mas", "nuevos"], ["mas", "nuevas"]];
const TOP_OLDEST = [["mas", "antiguos"], ["mas", "antiguas"], ["mas", "viejos"], ["mas", "viejas"]];

interface LimitResult {
  limit: number | null;
  sort: NlSort | null;
  notes: string[];
}

function interpretLimit(tokens: Token[], index: ColumnIndex[], original: string, first: boolean): LimitResult | null {
  const words = tokens.map((token) => token.fold);
  if (words[0] === "top") {
    const amount = integerToken(tokens[1]);
    if (!amount) return null;
    const tail = stripLeading(tokens.slice(2), new Set(["por", "de", "en", "segun", ...ARTICLES]));
    if (!tail.length) return { limit: amount, sort: null, notes: [] };
    const column = columnOrThrow(tail, index, original);
    return { limit: amount, sort: { column: column.name, direction: "desc" }, notes: [] };
  }
  if (words[0] === "limitar" || words[0] === "limita" || words[0] === "limite") {
    const amount = integerToken(tokens[words[1] === "a" ? 2 : 1]);
    return amount ? { limit: amount, sort: null, notes: [] } : null;
  }
  if ((words[0] === "primeros" || words[0] === "primeras") && integerToken(tokens[1])) {
    const rest = tokens.slice(2).filter((token) => !FILLER_NOUNS.has(token.fold));
    return rest.length ? null : { limit: integerToken(tokens[1]), sort: null, notes: [] };
  }
  const amount = integerToken(tokens[0]);
  if (!amount) return null;
  const after = tokens.slice(1);
  if (after.length && (after[0].fold === "primeros" || after[0].fold === "primeras")) {
    const rest = after.slice(1).filter((token) => !FILLER_NOUNS.has(token.fold));
    return rest.length ? null : { limit: amount, sort: null, notes: [] };
  }
  for (const [direction, phrases, temporal] of [
    ["desc", TOP_DESC, false],
    ["asc", TOP_ASC, false],
    ["desc", TOP_RECENT, true],
    ["asc", TOP_OLDEST, true],
  ] as const) {
    for (const phrase of phrases) {
      if (!matchWords(after, 0, phrase)) continue;
      const tail = stripLeading(after.slice(phrase.length), new Set(["por", "de", "en", "segun", ...ARTICLES]));
      if (!tail.length) {
        const dates = index.filter((item) => item.column.kind === "temporal").map((item) => item.column.name);
        throw new ParseIssue(
          temporal
            ? `Indica la columna de fecha: «los ${amount} más recientes por ${dates[0] ?? "fecha"}».${dates.length ? ` Columnas de fecha: ${dates.join(", ")}.` : ""}`
            : `Indica la columna: «los ${amount} mayores por salario».`,
        );
      }
      const column = columnOrThrow(tail, index, original);
      if (temporal && column.kind !== "temporal") {
        throw new ParseIssue(`${column.name} no es una fecha; usa «los ${amount} mayores por ${column.name}».`);
      }
      return { limit: amount, sort: { column: column.name, direction }, notes: [] };
    }
  }
  const rest = after.filter((token) => !FILLER_NOUNS.has(token.fold));
  if (!rest.length) return { limit: amount, sort: null, notes: [] };
  if (first && rest.length === 1 && isSubject(rest[0], index)) {
    return { limit: amount, sort: null, notes: [subjectNote(rest, original)] };
  }
  return null;
}

function isLatestClause(words: string[]): boolean {
  const text = words.join(" ");
  return (
    /\bcarga(s)? (mas reciente|reciente|actual)\b/.test(text) ||
    /\b(ultima|mas reciente) (carga|extraccion)\b/.test(text) ||
    /\bextraccion mas reciente\b/.test(text)
  );
}

function superlativeSort(tokens: Token[], index: ColumnIndex[]): NlSort | null {
  const words = tokens.map((token) => token.fold);
  let direction: "asc" | "desc" | null = null;
  let skip = 0;
  if (words[0] === "mayor" || (words[0] === "mas" && (words[1] === "alto" || words[1] === "alta"))) {
    direction = "desc";
    skip = words[0] === "mayor" ? 1 : 2;
  } else if (words[0] === "menor" || (words[0] === "mas" && (words[1] === "bajo" || words[1] === "baja"))) {
    direction = "asc";
    skip = words[0] === "menor" ? 1 : 2;
  }
  if (!direction) return null;
  const tail = stripLeading(tokens.slice(skip), new Set(["por", "de", "en", ...ARTICLES]));
  if (!tail.length) return null;
  const resolved = resolveColumn(tail, index);
  if (!("column" in resolved)) return null;
  return { column: resolved.column.name, direction };
}

function isSubject(token: Token, index: ColumnIndex[]): boolean {
  return token.type === "word" && SUBJECT_NOUNS.has(token.fold) && "missing" in resolveColumn([token], index);
}

function subjectNote(tokens: Token[], original: string): string {
  return `Se omitió «${sliceOf(original, tokens)}»: la fuente seleccionada ya define qué registros se muestran.`;
}

const GENERIC_HINT = "Prueba con «columna condición valor», p. ej. «salario mayor que 20000» o «nombre contiene Ana».";

export function parseNaturalLanguage(text: string, columns: NlColumn[], options: NlOptions = {}): NlParseResult {
  const result: NlParseResult = { filters: [], sort: [], limit: null, latestOnly: false, unrecognized: [], notes: [] };
  const original = text ?? "";
  if (!original.trim()) return result;
  const index = buildIndex(columns);
  const today = options.today ?? new Date();
  let lastWasSort = false;
  const pushSort = (sort: NlSort) => {
    if (result.sort.some((item) => item.column === sort.column)) {
      throw new ParseIssue(`${sort.column} ya está en el orden.`);
    }
    if (result.sort.length >= 3) throw new ParseIssue("Máximo 3 criterios de orden.");
    result.sort.push(sort);
  };

  for (const clause of splitClauses(tokenize(original))) {
    let tokens = stripLeading(clause.tokens, VERBS);
    tokens = stripLeading(tokens, ARTICLES);
    if (!tokens.length && !clause.prefix) continue;
    const display = sliceOf(original, clause.tokens) || clause.prefix || "";
    const words = tokens.map((token) => token.fold);
    try {
      if (!tokens.length) throw new ParseIssue(`Falta la columna después de «${clause.prefix}».`);
      if (isLatestClause(words)) {
        if (options.latestAvailable === false) {
          throw new ParseIssue("«Solo la carga más reciente» aplica únicamente a fuentes bronze.");
        }
        result.latestOnly = true;
        lastWasSort = false;
        continue;
      }
      if (SORT_WORDS.has(words[0])) {
        pushSort(interpretSort(tokens, index, original));
        lastWasSort = true;
        continue;
      }
      const limit = interpretLimit(tokens, index, original, clause.first);
      if (limit && limit.limit !== null && options.allowLimit === false) {
        throw new ParseIssue(
          "El límite de filas no aplica al guardar un conjunto de datos; para ordenar usa «ordenado por <columna> de mayor a menor».",
        );
      }
      if (limit) {
        if (limit.sort) pushSort(limit.sort);
        if (limit.limit !== null) result.limit = limit.limit;
        result.notes.push(...limit.notes);
        lastWasSort = false;
        continue;
      }
      if (clause.prefix) {
        const sort = superlativeSort(tokens, index);
        if (sort) {
          pushSort(sort);
          lastWasSort = false;
          continue;
        }
      }
      const suffix = directionSuffix(tokens);
      if (suffix.direction && suffix.body.length) {
        const resolved = resolveColumn(stripLeading(suffix.body, CONTINUATION_WORDS), index);
        if ("column" in resolved) {
          pushSort({ column: resolved.column.name, direction: suffix.direction });
          lastWasSort = true;
          continue;
        }
      }
      const candidates = operatorCandidates(tokens);
      let lastIssue: ParseIssue | null = null;
      let issueRank = -1;
      let interpreted: Interpreted | null = null;
      for (const candidate of candidates) {
        try {
          interpreted = interpretComparison(candidate, tokens, index, original, today);
          break;
        } catch (error) {
          if (!(error instanceof ParseIssue)) throw error;
          // Report the most advanced attempt: resolved column first, then explicit operator.
          const left = stripLeading(tokens.slice(0, candidate.start), new Set([...ARTICLES, ...COPULAS]));
          const resolved = left.length > 0 && "column" in resolveColumn(left, index);
          const rank = (resolved ? 2 : 0) + (candidate.op === "period" ? 0 : 1);
          if (rank > issueRank) {
            lastIssue = error;
            issueRank = rank;
          }
        }
      }
      if (interpreted) {
        result.filters.push(...interpreted.filters);
        result.notes.push(...interpreted.notes);
        lastWasSort = false;
        continue;
      }
      if (lastIssue) throw lastIssue;
      if (clause.prefix === "sin" || clause.prefix === "con") {
        const column = columnOrThrow(tokens, index, original);
        result.filters.push(filter(column, clause.prefix === "sin" ? "is_empty" : "is_not_empty"));
        lastWasSort = false;
        continue;
      }
      if (lastWasSort) {
        const { direction, body } = directionSuffix(tokens);
        const column = columnOrThrow(stripLeading(body, CONTINUATION_WORDS), index, original);
        pushSort({ column: column.name, direction: direction ?? "asc" });
        continue;
      }
      if (clause.first && tokens.length === 1 && isSubject(tokens[0], index)) {
        result.notes.push(subjectNote(tokens, original));
        continue;
      }
      const resolved = resolveColumn(tokens, index);
      if ("column" in resolved) {
        throw new ParseIssue(
          resolved.column.kind === "boolean"
            ? `Para filtrar por ${resolved.column.name} escribe «${resolved.column.name} es sí» o «${resolved.column.name} es no».`
            : `Mencionaste la columna ${resolved.column.name} sin condición; para elegir columnas usa el selector «Columnas».`,
        );
      }
      throw new ParseIssue(GENERIC_HINT);
    } catch (error) {
      if (!(error instanceof ParseIssue)) throw error;
      result.unrecognized.push({ text: display, hint: error.hint });
      lastWasSort = false;
    }
  }
  return result;
}
