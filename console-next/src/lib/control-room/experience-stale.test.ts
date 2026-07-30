import { describe, expect, it } from "vitest";

import {
  controlRoomExperienceSchema,
  controlRoomExperienceV2Schema,
} from "./experience-contract";

function v2Payload(staleValue: unknown, omit = false) {
  const fact: Record<string, unknown> = {
    kind: "kpi",
    title: "Rotación mensual",
    severity: "medium",
    observed_at: "2026-07-01T10:00:00Z",
    actions: [],
  };
  if (!omit) fact.stale = staleValue;
  return {
    schema_version: "control-room-experience/v2",
    generated_at: "2026-07-01T10:00:00Z",
    sections: [{ title: "KPIs", facts: [fact] }],
  };
}

function v1Payload(staleValue: unknown, omit = false) {
  const fact: Record<string, unknown> = {
    kind: "kpi",
    title: "Rotación mensual",
    severity: "medium",
    observed_at: "2026-07-01T10:00:00Z",
  };
  if (!omit) fact.stale = staleValue;
  return {
    schema_version: "control-room-experience/v1",
    generated_at: "2026-07-01T10:00:00Z",
    sections: [{ title: "KPIs", domain: "talento", facts: [fact] }],
  };
}

describe("contrato experience: stale nunca es vigente por omisión", () => {
  it("v2: stale ausente se representa como desconocido (null), no como vigente", () => {
    const parsed = controlRoomExperienceV2Schema.parse(v2Payload(undefined, true));
    expect(parsed.sections[0].facts[0].stale).toBeNull();
  });

  it("v2: stale null se conserva como desconocido", () => {
    const parsed = controlRoomExperienceV2Schema.parse(v2Payload(null));
    expect(parsed.sections[0].facts[0].stale).toBeNull();
  });

  it("v2: stale false y true se conservan literales", () => {
    expect(
      controlRoomExperienceV2Schema.parse(v2Payload(false)).sections[0].facts[0].stale,
    ).toBe(false);
    expect(
      controlRoomExperienceV2Schema.parse(v2Payload(true)).sections[0].facts[0].stale,
    ).toBe(true);
  });

  it("v1: ausente y null se representan como desconocido; booleanos literales", () => {
    expect(controlRoomExperienceSchema.parse(v1Payload(undefined, true)).sections[0].facts[0].stale).toBeNull();
    expect(controlRoomExperienceSchema.parse(v1Payload(null)).sections[0].facts[0].stale).toBeNull();
    expect(controlRoomExperienceSchema.parse(v1Payload(false)).sections[0].facts[0].stale).toBe(false);
    expect(controlRoomExperienceSchema.parse(v1Payload(true)).sections[0].facts[0].stale).toBe(true);
  });
});
