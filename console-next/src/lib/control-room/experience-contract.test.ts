import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { controlRoomExperienceSchema } from "./experience-contract";

const fixture = JSON.parse(
  readFileSync(
    resolve(process.cwd(), "../contracts/fixtures/control-room-experience-v1.json"),
    "utf8",
  ),
) as Record<string, unknown>;

describe("controlRoomExperienceSchema", () => {
  it("validates the shared Pydantic fixture", () => {
    const parsed = controlRoomExperienceSchema.parse(fixture);

    expect(parsed.schema_version).toBe("control-room-experience/v1");
    expect(parsed.sections[0].facts[0].metric?.value).toBe(0);
  });

  it("rejects unknown versions and unknown keys recursively", () => {
    expect(() =>
      controlRoomExperienceSchema.parse({
        ...fixture,
        schema_version: "control-room-experience/v2",
      }),
    ).toThrow();
    expect(() =>
      controlRoomExperienceSchema.parse({ ...fixture, source_url: "https://internal" }),
    ).toThrow();
    expect(() =>
      controlRoomExperienceSchema.parse({
        ...fixture,
        scope: { tenant_id: "private", workspace_id: "private" },
      }),
    ).toThrow();

    const nested = structuredClone(fixture) as {
      sections: Array<Record<string, unknown> & { facts: Array<Record<string, unknown>> }>;
    };
    nested.sections[0].module_id = "technical-module";
    nested.sections[0].facts[0].payload_hash = "technical";
    expect(() => controlRoomExperienceSchema.parse(nested)).toThrow();
  });

  it("rejects invalid dates and non-finite metric values", () => {
    expect(() =>
      controlRoomExperienceSchema.parse({ ...fixture, generated_at: "not-a-date" }),
    ).toThrow();

    const nonFinite = structuredClone(fixture) as {
      sections: Array<{ facts: Array<{ metric: { value: number } }> }>;
    };
    nonFinite.sections[0].facts[0].metric.value = Number.POSITIVE_INFINITY;
    expect(() => controlRoomExperienceSchema.parse(nonFinite)).toThrow();
  });

  it("accepts omitted optional fields without manufacturing substitutes", () => {
    const parsed = controlRoomExperienceSchema.parse({
      schema_version: "control-room-experience/v1",
      generated_at: "2026-07-25T12:30:00Z",
      sections: [
        {
          title: "Performance",
          domain: "Personas",
          facts: [
            {
              kind: "kpi",
              title: "Cobertura",
              severity: "low",
              observed_at: "2026-07-24T00:00:00Z",
            },
          ],
        },
      ],
    });

    expect(parsed.sections[0].facts[0]).toEqual(
      expect.objectContaining({ stale: false }),
    );
    expect(parsed.sections[0].facts[0].metric).toBeUndefined();
  });
});
