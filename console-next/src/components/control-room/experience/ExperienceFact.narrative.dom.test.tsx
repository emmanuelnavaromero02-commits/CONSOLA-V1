// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  controlRoomExperienceV2Schema,
  type ExperienceFactV2,
} from "@/lib/control-room/experience-contract";

import { ExperienceFact } from "./ExperienceFact";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const forbiddenCopy = /Aprobar|Ejecutar|Sí, ejecutar/;
const advisory = "Solo recomendación: nada se aplica automáticamente.";

const narrative = {
  status: "ready",
  explanation: "La cobertura bajó tres puntos frente al mes anterior.",
  recommendation: "Revisar con el owner de la región las vacantes abiertas.",
  confidence_label: "media",
  confidence_reason: "El dato cubre solo dos de las tres regiones.",
  basis_note: "Basado en el agregado mensual de posiciones críticas.",
  evidence_note: "Evidencia verificada el 24 de julio.",
  limitations: ["No incluye contratistas.", "Sin datos de la Región Sur."],
};

const baseFact = {
  kind: "kpi",
  title: "Cobertura crítica",
  severity: "low",
  observed_at: "2026-07-24T00:00:00Z",
  stale: false,
  entity_label: "Región Norte",
  metric: { name: "Cobertura", kind: "count", value: 18 },
  actions: [
    {
      action_handle: "a".repeat(64),
      label: "Solicitar revisión de owner",
      enabled: true,
      requires_approval: true,
    },
  ],
};

function parseFact(raw: unknown): ExperienceFactV2 {
  return controlRoomExperienceV2Schema.parse({
    schema_version: "control-room-experience/v2",
    generated_at: "2026-07-25T12:30:00Z",
    sections: [{ title: "Performance", facts: [raw] }],
  }).sections[0].facts[0];
}

let container: HTMLDivElement;
let root: Root;

async function renderFact(fact: ExperienceFactV2) {
  await act(async () => {
    root.render(<ExperienceFact fact={fact} onPreviewAction={vi.fn()} />);
  });
}

function narrativeGroup() {
  return container.querySelector('[role="group"][aria-label="Lectura de negocio"]');
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("ExperienceFact: lectura de negocio opcional", () => {
  it("sin narrativa renderiza el dato igual que antes y la narrativa es solo aditiva", async () => {
    await renderFact(parseFact(baseFact));
    const markupWithout = container.innerHTML;

    expect(narrativeGroup()).toBeNull();
    for (const copy of ["Recomendación", "Confianza", "Limitaciones", advisory]) {
      expect(container.textContent).not.toContain(copy);
    }

    await renderFact(parseFact({ ...baseFact, narrative }));
    expect(narrativeGroup()).not.toBeNull();
    const withoutBlock = container.cloneNode(true) as HTMLElement;
    withoutBlock.querySelector('[role="group"]')?.remove();
    expect(withoutBlock.innerHTML).toBe(markupWithout);
  });

  it("renderiza explicación, recomendación, confianza, notas y limitaciones", async () => {
    await renderFact(parseFact({ ...baseFact, narrative }));
    const group = narrativeGroup();
    const text = group?.textContent ?? "";

    expect(text).toContain(narrative.explanation);
    expect(text).toContain("Recomendación");
    expect(text).toContain(narrative.recommendation);
    expect(text).toContain("Confianza: media");
    expect(text).toContain(narrative.confidence_reason);
    expect(text).toContain(narrative.basis_note);
    expect(text).toContain(narrative.evidence_note);
    expect(text).toContain(advisory);

    const list = group?.querySelector("ul");
    const items = [...(list?.querySelectorAll("li") ?? [])].map(
      (item) => item.textContent,
    );
    expect(items).toEqual(narrative.limitations);
    const labelId = list?.getAttribute("aria-labelledby");
    expect(labelId).toBeTruthy();
    expect(document.getElementById(labelId ?? "")?.textContent).toBe("Limitaciones");
  });

  it("omite los campos opcionales ausentes y no muestra limitaciones vacías", async () => {
    await renderFact(
      parseFact({
        ...baseFact,
        narrative: {
          status: "template",
          explanation: "Explicación de plantilla.",
          recommendation: "Recomendación de plantilla.",
          confidence_label: "baja",
          basis_note: "Basado en el agregado disponible.",
          limitations: [],
        },
      }),
    );
    const group = narrativeGroup();

    expect(group?.textContent).toContain("Explicación de plantilla.");
    expect(group?.textContent).toContain("Confianza: baja");
    expect(group?.textContent).toContain(advisory);
    expect(group?.textContent).not.toContain("Limitaciones");
    expect(group?.querySelector("ul")).toBeNull();
    expect(group?.querySelectorAll("p")).toHaveLength(6);
  });

  it("renderiza el texto del backend como texto, nunca como HTML", async () => {
    const markup = '<img src="x" onerror="alert(1)"><b>negrita</b>';
    await renderFact(
      parseFact({ ...baseFact, narrative: { ...narrative, explanation: markup } }),
    );

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(narrativeGroup()?.textContent).toContain(markup);
  });

  it("no introduce copy de aprobación o ejecución ni controles propios", async () => {
    await renderFact(parseFact({ ...baseFact, narrative }));

    expect(container.textContent).not.toMatch(forbiddenCopy);
    expect(narrativeGroup()?.querySelectorAll("button, a, input")).toHaveLength(0);
    expect(container.querySelectorAll("button")).toHaveLength(1);
  });
});
