import { describe, expect, it } from "vitest";

import {
  controlRoomExperienceV2Schema,
  experienceActionPreviewResponseSchema,
} from "./experience-contract";

const handle = "a".repeat(64);

const action = {
  action_handle: handle,
  label: "Solicitar revisión de owner",
  enabled: true,
  requires_approval: true,
};

const fact = {
  kind: "kpi",
  title: "Cobertura de posiciones críticas",
  severity: "low",
  observed_at: "2026-07-24T00:00:00Z",
  stale: false,
  entity_label: "Región Norte",
  metric: { name: "Cobertura", kind: "count", value: 18 },
  decision: { status: "decision_created" },
  actions: [action],
};

const payload = {
  schema_version: "control-room-experience/v2",
  generated_at: "2026-07-25T12:30:00Z",
  sections: [{ title: "Performance", facts: [fact] }],
};

describe("controlRoomExperienceV2Schema", () => {
  it("accepts the exact public V2 contract", () => {
    expect(controlRoomExperienceV2Schema.parse(payload)).toEqual(payload);
  });

  it.each([
    ["response metadata", { ...payload, metadata: { private: true } }],
    [
      "section domain",
      { ...payload, sections: [{ ...payload.sections[0], domain: "Personas" }] },
    ],
    [
      "fact item_id",
      {
        ...payload,
        sections: [
          {
            ...payload.sections[0],
            facts: [{ ...fact, item_id: "private-item" }],
          },
        ],
      },
    ],
    [
      "action endpoint",
      {
        ...payload,
        sections: [
          {
            ...payload.sections[0],
            facts: [{ ...fact, actions: [{ ...action, endpoint: "/execute" }] }],
          },
        ],
      },
    ],
    [
      "action binding",
      {
        ...payload,
        sections: [
          {
            ...payload.sections[0],
            facts: [{ ...fact, actions: [{ ...action, binding_id: handle }] }],
          },
        ],
      },
    ],
  ])("rejects private or extra %s", (_name, invalid) => {
    expect(() => controlRoomExperienceV2Schema.parse(invalid)).toThrow();
  });

  it.each(["a".repeat(63), "A".repeat(64), "g".repeat(64)])(
    "rejects invalid action handles",
    (actionHandle) => {
      const invalid = structuredClone(payload);
      invalid.sections[0].facts[0].actions[0].action_handle = actionHandle;
      expect(() => controlRoomExperienceV2Schema.parse(invalid)).toThrow();
    },
  );

  it("rejects invalid action state, duplicate handles and more than eight actions", () => {
    for (const actions of [
      [{ ...action, enabled: false }],
      [{ ...action, disabled_reason: "Actualiza los datos antes de continuar." }],
      [action, action],
      Array.from({ length: 9 }, (_, index) => ({
        ...action,
        action_handle: index.toString(16).padStart(64, "0"),
      })),
    ]) {
      expect(() =>
        controlRoomExperienceV2Schema.parse({
          ...payload,
          sections: [{ ...payload.sections[0], facts: [{ ...fact, actions }] }],
        }),
      ).toThrow();
    }
  });

  it("rejects empty or oversized labels and unknown disabled reasons", () => {
    for (const candidate of [
      { ...action, label: "" },
      { ...action, label: "x".repeat(121) },
      { ...action, enabled: false, disabled_reason: "Detalle interno" },
    ]) {
      expect(() =>
        controlRoomExperienceV2Schema.parse({
          ...payload,
          sections: [
            { ...payload.sections[0], facts: [{ ...fact, actions: [candidate] }] },
          ],
        }),
      ).toThrow();
    }
  });
});

describe("experienceActionPreviewResponseSchema", () => {
  const response = {
    action_handle: handle,
    operation: "preview",
    status: "generated",
    message: "Preview generado; no se ejecuto ningun cambio externo.",
  };

  it("accepts only the exact public preview response", () => {
    expect(experienceActionPreviewResponseSchema.parse(response)).toEqual(response);
  });

  it.each([
    { ...response, status: "executed" },
    { ...response, operation: "execute" },
    { ...response, action_handle: "invalid" },
    { ...response, metadata: { binding_id: handle } },
  ])("fails closed on invalid or unknown response state", (invalid) => {
    expect(() => experienceActionPreviewResponseSchema.parse(invalid)).toThrow();
  });
});
