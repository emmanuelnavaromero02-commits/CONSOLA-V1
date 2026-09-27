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
  composeFallbackReading,
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
  actions: [],
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
