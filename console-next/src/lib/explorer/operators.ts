export type ColumnKind = "text" | "number" | "temporal" | "boolean" | "other";

export type ExplorerOp =
  | "eq"
  | "neq"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "between"
  | "contains"
  | "not_contains"
  | "starts_with"
  | "is_empty"
  | "is_not_empty"
  | "in";

export type OperatorArity = "none" | "one" | "two" | "many";

export const OPERATOR_LABELS: Record<ExplorerOp, string> = {
  eq: "es igual a",
  neq: "es distinto de",
  gt: "es mayor que",
  gte: "es mayor o igual que",
  lt: "es menor que",
  lte: "es menor o igual que",
  between: "está entre",
  contains: "contiene",
  not_contains: "no contiene",
  starts_with: "empieza con",
  is_empty: "está vacío",
  is_not_empty: "no está vacío",
  in: "es uno de",
};

export const KIND_LABELS: Record<ColumnKind, string> = {
  text: "texto",
  number: "número",
  temporal: "fecha",
  boolean: "sí/no",
  other: "estructura",
};

const RANGE_OPS: ExplorerOp[] = ["eq", "neq", "gt", "gte", "lt", "lte", "between", "in", "is_empty", "is_not_empty"];

const KIND_OPERATORS: Record<ColumnKind, ExplorerOp[]> = {
  text: ["contains", "eq", "neq", "starts_with", "not_contains", "in", "is_empty", "is_not_empty"],
  number: RANGE_OPS,
  temporal: RANGE_OPS,
  boolean: ["eq", "neq", "is_empty", "is_not_empty"],
  other: ["is_empty", "is_not_empty"],
};

const SORTABLE_KINDS = new Set<ColumnKind>(["text", "number", "temporal", "boolean"]);

export function operatorsForKind(kind: ColumnKind): ExplorerOp[] {
  return KIND_OPERATORS[kind] ?? KIND_OPERATORS.other;
}

export function operatorAllowed(op: ExplorerOp, kind: ColumnKind): boolean {
  return operatorsForKind(kind).includes(op);
}

export function operatorArity(op: ExplorerOp): OperatorArity {
  if (op === "is_empty" || op === "is_not_empty") return "none";
  if (op === "between") return "two";
  if (op === "in") return "many";
  return "one";
}

export function isSortableKind(kind: ColumnKind): boolean {
  return SORTABLE_KINDS.has(kind);
}

export function isExplorerOp(value: unknown): value is ExplorerOp {
  return typeof value === "string" && value in OPERATOR_LABELS;
}
