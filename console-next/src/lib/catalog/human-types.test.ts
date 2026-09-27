import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import type { SemanticType } from "@/lib/data/types";

import {
  HUMAN_TYPE_LABEL,
  SEMANTIC_TYPES,
  columnSemanticType,
  humanTypeLabel,
  normalizeName,
  semanticTypeFromRaw,
  splitTokens,
} from "./human-types";

interface FixtureCase {
  raw_type: string;
  column: string;
  is_key: boolean;
  semantic_type: SemanticType;
}

const fixture = JSON.parse(
  readFileSync(resolve(process.cwd(), "../contracts/fixtures/catalog-human-types-v1.json"), "utf8"),
) as { schema_version: string; labels: Record<string, string>; cases: FixtureCase[] };

describe("human types", () => {
  it("shares the labels with the Python rules through the fixture", () => {
    expect(fixture.schema_version).toBe("catalog-human-types/v1");
    expect(HUMAN_TYPE_LABEL).toEqual(fixture.labels);
    expect(new Set(SEMANTIC_TYPES)).toEqual(new Set(Object.keys(fixture.labels)));
  });

  it.each(fixture.cases)("maps $raw_type $column like refinement", (item) => {
    expect(semanticTypeFromRaw(item.raw_type, item.column, item.is_key)).toBe(item.semantic_type);
  });

  it("covers every semantic type in the shared cases", () => {
    expect(new Set(fixture.cases.map((item) => item.semantic_type))).toEqual(new Set(SEMANTIC_TYPES));
  });

  it("prefers the Copilot semantic type and falls back to the raw type", () => {
    expect(columnSemanticType({ name: "x", type: "VARCHAR", semantic_type: "money" })).toBe("money");
    expect(columnSemanticType({ name: "x", type: "VARCHAR", semantic_type: "bogus" as SemanticType })).toBe("text");
    expect(humanTypeLabel({ name: "employee_id", type: "BIGINT" })).toBe("Identificador");
    expect(humanTypeLabel({ name: "hire_date", type: "DATE" })).toBe("Fecha");
    expect(humanTypeLabel({ name: "department", type: "VARCHAR", is_key: true })).toBe("Identificador");
  });

  it("tokenises camelCase and snake_case like the Python rules", () => {
    expect(splitTokens("personIdExternal")).toEqual(["person", "id", "external"]);
    expect(splitTokens("HTTPStatus_code")).toEqual(["http", "status", "code"]);
    expect(normalizeName("Fecha_Ñandú-1")).toBe("fechañandú1");
  });
});
