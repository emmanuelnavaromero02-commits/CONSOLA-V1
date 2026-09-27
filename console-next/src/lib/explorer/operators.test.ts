import { describe, expect, it } from "vitest";

import {
  KIND_LABELS,
  OPERATOR_LABELS,
  isExplorerOp,
  isSortableKind,
  operatorAllowed,
  operatorArity,
  operatorsForKind,
} from "./operators";

describe("explorer operators", () => {
  it("uses the business Spanish labels", () => {
    expect(Object.values(OPERATOR_LABELS)).toEqual([
      "es igual a",
      "es distinto de",
      "es mayor que",
      "es mayor o igual que",
      "es menor que",
      "es menor o igual que",
      "está entre",
      "contiene",
      "no contiene",
      "empieza con",
      "está vacío",
      "no está vacío",
      "es uno de",
    ]);
    expect(KIND_LABELS.temporal).toBe("fecha");
  });

  it("offers only operators the backend accepts for each column type", () => {
    expect(operatorsForKind("text")).not.toContain("gt");
    expect(operatorsForKind("number")).toContain("between");
    expect(operatorsForKind("number")).not.toContain("contains");
    expect(operatorsForKind("temporal")).toContain("lte");
    expect(operatorsForKind("boolean")).toEqual(["eq", "neq", "is_empty", "is_not_empty"]);
    expect(operatorsForKind("other")).toEqual(["is_empty", "is_not_empty"]);
    expect(operatorAllowed("starts_with", "text")).toBe(true);
    expect(operatorAllowed("starts_with", "number")).toBe(false);
  });

  it("describes how many values each operator needs", () => {
    expect(operatorArity("is_empty")).toBe("none");
    expect(operatorArity("between")).toBe("two");
    expect(operatorArity("in")).toBe("many");
    expect(operatorArity("contains")).toBe("one");
  });

  it("guards operator names and sortable kinds", () => {
    expect(isExplorerOp("eq")).toBe(true);
    expect(isExplorerOp("raw_sql")).toBe(false);
    expect(isExplorerOp(3)).toBe(false);
    expect(isSortableKind("other")).toBe(false);
    expect(isSortableKind("temporal")).toBe(true);
  });
});
