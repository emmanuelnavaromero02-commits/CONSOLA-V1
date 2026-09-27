// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ExperienceFactV2 } from "@/lib/control-room/experience-contract";
import { formatObservedAt } from "@/lib/control-room/experience-presenter";

import {
  FALLBACK_READING_LABEL,
  FALLBACK_RECOMMENDATION,
  FactFallbackReading,
  NEUTRAL_RECOMMENDATION,
  composeFallbackReading,
  fallbackRecommendation,
} from "./FactFallbackReading";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const fact = {
  kind: "anomaly",
  title: "Empleado terminado activo",
  severity: "critical",
  observed_at: "2026-09-20T00:00:00Z",
  stale: false,
  entity_label: "Ana Gómez",
  metric: { name: "Salario mensual", kind: "amount", value: 4200, unit: "USD" },
  actions: [
    {
      action_handle: "a".repeat(64),
      kind: "exception_approval",
      label: "Aprobar Excepción",
      enabled: true,
      requires_approval: false,
    },
    {
      action_handle: "b".repeat(64),
      kind: "studio_adjustment",
      label: "Ajustar en Estudio",
      enabled: true,
      requires_approval: false,
    },
    {
      action_handle: "c".repeat(64),
      kind: "decision_proposal",
      label: "Crear Propuesta de Decisión",
      enabled: false,
      requires_approval: false,
      disabled_reason: "Actualiza los datos antes de continuar.",
    },
  ],
} as ExperienceFactV2;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("fallbackRecommendation", () => {
  it("names only the actions the fact actually carries", () => {
    expect(fallbackRecommendation(fact)).toBe(FALLBACK_RECOMMENDATION);
    expect(fallbackRecommendation({ ...fact, actions: [fact.actions[0]] })).toBe(
      "Revisa la evidencia y decide si aprobar una excepción.",
    );
    expect(
      fallbackRecommendation({ ...fact, actions: [fact.actions[2], fact.actions[1]] }),
    ).toBe("Revisa la evidencia y decide si ajustarlo en Estudio o crear una propuesta de decisión.");
  });

  it("stays neutral for readers, facts without actions and stale facts", () => {
    expect(fallbackRecommendation({ ...fact, actions: [] })).toBe(NEUTRAL_RECOMMENDATION);
    expect(fallbackRecommendation({ ...fact, stale: true })).toBe(NEUTRAL_RECOMMENDATION);
    expect(NEUTRAL_RECOMMENDATION).not.toMatch(/aprobar|Estudio|propuesta/i);
  });
});

describe("FactFallbackReading", () => {
  it("is labelled as automatic and composed only from present fields", async () => {
    await act(async () => {
      root.render(<FactFallbackReading fact={fact} sectionTitle="Personas" />);
    });
    const group = container.querySelector(
      `[role="group"][aria-label="${FALLBACK_READING_LABEL}"]`,
    );

    expect(group?.textContent).toContain(FALLBACK_READING_LABEL);
    expect(group?.textContent).toContain(
      "Anomalía de severidad crítica en Personas para Ana Gómez.",
    );
    expect(group?.textContent).toContain("Salario mensual: 4,200 USD.");
    expect(group?.textContent).toContain(
      `Dato del ${formatObservedAt("2026-09-20T00:00:00Z")}.`,
    );
    expect(group?.textContent).toContain(FALLBACK_RECOMMENDATION);
    expect(group?.querySelectorAll("button, a, input")).toHaveLength(0);
  });

  it("omits absent entity, metric and section instead of inventing them", () => {
    const minimal = {
      ...fact,
      kind: "kpi",
      severity: "low",
      entity_label: undefined,
      metric: undefined,
    } as ExperienceFactV2;

    expect(composeFallbackReading(minimal, null)).toEqual([
      "Indicador de severidad baja.",
      `Dato del ${formatObservedAt("2026-09-20T00:00:00Z")}.`,
    ]);
    expect(composeFallbackReading(minimal, "  ")[0]).toBe("Indicador de severidad baja.");
  });

  it("renders server strings as text, never HTML", async () => {
    await act(async () => {
      root.render(
        <FactFallbackReading
          fact={{ ...fact, entity_label: '<img src="x" onerror="alert(1)">' }}
          sectionTitle="<b>Personas</b>"
        />,
      );
    });

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
  });
});
