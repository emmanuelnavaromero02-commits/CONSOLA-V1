import { describe, expect, it } from "vitest";

import type {
  ExperienceFactV2,
  ExperienceSectionV2,
} from "./experience-contract";
import { buildSectionHighlights, joinEntityLabels } from "./experience-headlines";
import { formatObservedAt } from "./experience-presenter";

const SEPT_20 = `Dato del ${formatObservedAt("2026-09-20T00:00:00Z")}`;

function fact(overrides: Partial<ExperienceFactV2> = {}): ExperienceFactV2 {
  return {
    kind: "anomaly",
    title: "Empleado terminado activo",
    severity: "high",
    observed_at: "2026-09-20T00:00:00Z",
    stale: false,
    actions: [],
    ...overrides,
  } as ExperienceFactV2;
}

function section(facts: ExperienceFactV2[]): ExperienceSectionV2 {
  return { title: "Personas", facts };
}

function words(value: string): Set<string> {
  return new Set(value.toLocaleLowerCase("es-MX").match(/[\p{L}]+/gu) ?? []);
}

describe("buildSectionHighlights", () => {
  it("keeps a single fact title as the headline without extra copy", () => {
    const [highlight] = buildSectionHighlights(section([fact()]));

    expect(highlight.headline).toBe("Empleado terminado activo");
    expect(highlight.count).toBe(1);
    expect(highlight.subtitle).toBe(SEPT_20);
  });

  it("groups by kind and case-insensitive title and counts cases", () => {
    const highlights = buildSectionHighlights(
      section([
        fact({ entity_label: "Ana" }),
        fact({ title: "EMPLEADO TERMINADO ACTIVO", entity_label: "Luis" }),
        fact({ kind: "signal", entity_label: "Eva" }),
      ]),
    );

    expect(highlights.map((value) => value.headline)).toEqual([
      "2 casos detectados: Empleado terminado activo",
      "Empleado terminado activo",
    ]);
    expect(highlights[0].subtitle).toBe(`Ana y Luis · ${SEPT_20}`);
  });

  it("summarises one metric as a value and several as a range", () => {
    const metric = { name: "Salario mensual", kind: "amount" as const, unit: "USD" };
    const [single] = buildSectionHighlights(
      section([fact({ metric: { ...metric, value: 4200 } })]),
    );
    const [range] = buildSectionHighlights(
      section([
        fact({ metric: { ...metric, value: 4200 } }),
        fact({ metric: { ...metric, value: 1800 } }),
      ]),
    );

    expect(single.subtitle).toContain("Salario mensual: 4,200 USD");
    expect(range.subtitle).toContain("Salario mensual: de 1,800 USD a 4,200 USD");
  });

  it("omits metrics when any case lacks one or names differ", () => {
    const [missing] = buildSectionHighlights(
      section([
        fact({ metric: { name: "Horas", kind: "count", value: 3 } }),
        fact(),
      ]),
    );
    const [mixed] = buildSectionHighlights(
      section([
        fact({ metric: { name: "Horas", kind: "count", value: 3 } }),
        fact({ metric: { name: "Días", kind: "count", value: 3 } }),
      ]),
    );

    expect(missing.subtitle).toBe(SEPT_20);
    expect(mixed.subtitle).toBe(SEPT_20);
  });

  it("uses the latest observation date of the group", () => {
    const [highlight] = buildSectionHighlights(
      section([
        fact({ observed_at: "2026-09-01T00:00:00Z" }),
        fact({ observed_at: "2026-09-18T00:00:00Z" }),
      ]),
    );

    expect(highlight.subtitle).toBe(`Dato del ${formatObservedAt("2026-09-18T00:00:00Z")}`);
  });

  it("never invents words beyond the fixed templates and present fields", () => {
    const facts = [
      fact({ entity_label: "Planta Norte", metric: { name: "Merma", kind: "count", value: 2 } }),
      fact({ entity_label: "Planta Sur", metric: { name: "Merma", kind: "count", value: 5 } }),
      fact({ entity_label: "Planta Este", metric: { name: "Merma", kind: "count", value: 9 } }),
      fact({ entity_label: "Planta Oeste", metric: { name: "Merma", kind: "count", value: 9 } }),
    ];
    const allowed = new Set([
      ...words(`casos detectados de a y más dato del ${formatObservedAt("2026-09-20T00:00:00Z")}`),
      ...facts.flatMap((value) => [...words(`${value.title} ${value.entity_label}`)]),
      ...words("Merma"),
    ]);
    for (const highlight of buildSectionHighlights(section(facts))) {
      for (const word of words(`${highlight.headline} ${highlight.subtitle ?? ""}`)) {
        expect(allowed.has(word), word).toBe(true);
      }
    }
  });
});

describe("joinEntityLabels", () => {
  it.each([
    [[], null],
    [["A"], "A"],
    [["A", "B"], "A y B"],
    [["A", "B", "C"], "A, B y C"],
    [["A", "B", "C", "D", "E"], "A, B y 3 más"],
    [["A", "A", " "], "A"],
  ])("joins %j", (labels, expected) => {
    expect(joinEntityLabels(labels)).toBe(expected);
  });
});
