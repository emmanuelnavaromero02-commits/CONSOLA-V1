import { describe, expect, it } from "vitest";

import {
  controlRoomExperienceV2Schema,
  experienceActionPreviewResponseSchema,
  experienceNarrativeSchema,
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

  it.each([
    "item_id",
    "template_id",
    "method",
    "scope",
    "tenant",
    "workspace",
    "dataset",
    "metadata",
    "provenance",
  ])("rejects the private fact field %s", (privateField) => {
    expect(() =>
      controlRoomExperienceV2Schema.parse({
        ...payload,
        sections: [
          {
            ...payload.sections[0],
            facts: [{ ...fact, [privateField]: "private" }],
          },
        ],
      }),
    ).toThrow();
  });
});

describe("experience fact narrative", () => {
  const narrative = {
    status: "ready",
    explanation: "La cobertura bajó tres puntos frente al mes anterior.",
    recommendation: "Revisar con el owner de la región las vacantes abiertas.",
    confidence_label: "media",
    confidence_reason: "El dato cubre solo dos de las tres regiones.",
    basis_note: "Basado en el agregado mensual de posiciones críticas.",
    evidence_note: "Evidencia verificada el 24 de julio.",
    limitations: ["No incluye contratistas."],
  };

  function withNarrative(candidate: unknown) {
    return {
      ...payload,
      sections: [
        { ...payload.sections[0], facts: [{ ...fact, narrative: candidate }] },
      ],
    };
  }

  function omit(key: string) {
    return Object.fromEntries(
      Object.entries(narrative).filter(([name]) => name !== key),
    );
  }

  it("keeps a fact without narrative valid and adds no narrative key", () => {
    const parsed = controlRoomExperienceV2Schema.parse(payload);
    expect(parsed.sections[0].facts[0]).not.toHaveProperty("narrative");
  });

  it("accepts a fact with the full narrative contract", () => {
    const candidate = withNarrative(narrative);
    expect(controlRoomExperienceV2Schema.parse(candidate)).toEqual(candidate);
  });

  it("accepts a template narrative without optional fields at the exact bounds", () => {
    const bounded = {
      status: "template",
      explanation: "x".repeat(600),
      recommendation: "x".repeat(600),
      confidence_label: "baja",
      basis_note: "x".repeat(240),
      limitations: Array.from({ length: 4 }, () => "x".repeat(240)),
    };
    expect(experienceNarrativeSchema.parse(bounded)).toEqual(bounded);
    expect(
      experienceNarrativeSchema.parse({
        ...bounded,
        confidence_reason: "x".repeat(600),
        evidence_note: "x".repeat(240),
        limitations: [],
      }),
    ).toBeTruthy();
  });

  it("measures text bounds in characters like the backend, not UTF-16 units", () => {
    expect(
      experienceNarrativeSchema.parse({ ...narrative, basis_note: "😀".repeat(240) }),
    ).toBeTruthy();
    expect(() =>
      experienceNarrativeSchema.parse({ ...narrative, basis_note: "😀".repeat(241) }),
    ).toThrow();
  });

  it.each(["alta", "media", "baja"])("accepts the confidence level %s", (level) => {
    expect(
      experienceNarrativeSchema.parse({ ...narrative, confidence_label: level })
        .confidence_label,
    ).toBe(level);
  });

  it.each(["Media", "MEDIA", "high", "muy alta", "alta ", "", "desconocida"])(
    "rejects the confidence label %j outside alta/media/baja",
    (label) => {
      expect(() =>
        controlRoomExperienceV2Schema.parse(
          withNarrative({ ...narrative, confidence_label: label }),
        ),
      ).toThrow();
    },
  );

  it.each(["prompt", "model", "sources", "item_id", "metadata"])(
    "rejects the unknown narrative field %s",
    (field) => {
      expect(() =>
        controlRoomExperienceV2Schema.parse(
          withNarrative({ ...narrative, [field]: "private" }),
        ),
      ).toThrow();
    },
  );

  it("rejects more than four limitations", () => {
    expect(() =>
      controlRoomExperienceV2Schema.parse(
        withNarrative({
          ...narrative,
          limitations: Array.from({ length: 5 }, (_, index) => `Limitación ${index}`),
        }),
      ),
    ).toThrow();
  });

  it.each([
    ["an unknown status", { ...narrative, status: "draft" }],
    ["an empty explanation", { ...narrative, explanation: "" }],
    ["an oversized explanation", { ...narrative, explanation: "x".repeat(601) }],
    ["an oversized recommendation", { ...narrative, recommendation: "x".repeat(601) }],
    ["an oversized confidence reason", { ...narrative, confidence_reason: "x".repeat(601) }],
    ["an empty basis note", { ...narrative, basis_note: "" }],
    ["an oversized basis note", { ...narrative, basis_note: "x".repeat(241) }],
    ["an empty evidence note", { ...narrative, evidence_note: "" }],
    ["an oversized evidence note", { ...narrative, evidence_note: "x".repeat(241) }],
    ["an empty limitation", { ...narrative, limitations: [""] }],
    ["an oversized limitation", { ...narrative, limitations: ["x".repeat(241)] }],
    ["a missing recommendation", omit("recommendation")],
    ["missing limitations", omit("limitations")],
    ["a null narrative", null],
  ])("rejects a narrative with %s", (_name, candidate) => {
    expect(() =>
      controlRoomExperienceV2Schema.parse(withNarrative(candidate)),
    ).toThrow();
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
