import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import {
  controlRoomExperienceSchema,
  controlRoomExperienceV2Schema,
} from "@/lib/control-room/experience-contract";

import {
  ControlRoomExperienceContent,
} from "./ControlRoomExperiencePage";
import { ExperienceLoadState } from "./ExperienceLoadState";

const legacyExperience = controlRoomExperienceSchema.parse(
  JSON.parse(
    readFileSync(
      resolve(process.cwd(), "../contracts/fixtures/control-room-experience-v1.json"),
      "utf8",
    ),
  ),
);
const experience = controlRoomExperienceV2Schema.parse({
  schema_version: "control-room-experience/v2",
  generated_at: legacyExperience.generated_at,
  sections: legacyExperience.sections.map(({ title, facts }) => ({
    title,
    facts: facts.map((fact) => ({ ...fact, actions: [] })),
  })),
});

function renderContent(overrides = {}) {
  return renderToStaticMarkup(
    <ControlRoomExperienceContent
      experience={experience}
      refreshing={false}
      refreshFailed={false}
      onRefresh={vi.fn()}
      onPreviewAction={vi.fn()}
      {...overrides}
    />,
  );
}

describe("ControlRoomExperienceContent", () => {
  it("renders only sections that contain facts", () => {
    const markup = renderContent({
      experience: {
        ...experience,
        sections: [
          ...experience.sections,
          { ...experience.sections[0], title: "Competencias", facts: [] },
        ],
      },
    });

    expect(markup).toContain("Performance");
    expect(markup).not.toContain("Competencias");
  });

  it("keeps a real zero, stale fact and observation date visible", () => {
    const markup = renderContent();

    expect(markup).toContain("0%");
    expect(markup).toContain("Información anterior");
    expect(markup).toContain('dateTime="2026-04-01T00:00:00Z"');
  });

  it("omits absent optional fields and technical identity", () => {
    const markup = renderContent();

    expect(markup).not.toContain("N/D");
    expect(markup).not.toContain("fixture-cartridge");
    expect(markup).not.toContain("fixture-module");
    expect(markup).not.toContain("opaque-performance-key");
    expect(markup).not.toContain("tenant-fixture");
    expect(markup).not.toContain("workspace-fixture");
  });

  it("renders decisions as information without reference or action CTA", () => {
    const markup = renderContent();

    expect(markup).toContain("Decisión registrada");
    expect(markup).not.toContain("reference");
    expect(markup).not.toContain("Aprobar");
    expect(markup).not.toContain("Ejecutar");
    expect(markup).not.toContain("Vista previa");
  });

  it("does not manufacture a CTA when actions are empty", () => {
    const markup = renderContent();

    expect(markup).not.toContain("Generar preview");
    expect(markup).not.toContain("Confirmar preview");
  });

  it("renders a disabled server action and its exact reason without enabling it", () => {
    const firstFact = experience.sections[0].facts[0];
    const markup = renderContent({
      experience: {
        ...experience,
        sections: [
          {
            ...experience.sections[0],
            facts: [
              {
                ...firstFact,
                actions: [
                  {
                    action_handle: "a".repeat(64),
                    label: "Solicitar revisión de owner",
                    enabled: false,
                    requires_approval: true,
                    disabled_reason: "Actualiza los datos antes de continuar.",
                  },
                ],
              },
            ],
          },
        ],
      },
    });

    expect(markup).toContain("Solicitar revisión de owner");
    expect(markup).toContain("Actualiza los datos antes de continuar.");
    expect(markup).toContain("Generar preview");
    expect(markup).toContain("disabled");
    expect(markup).not.toContain("a".repeat(64));
  });

  it("keeps the last payload after a failed manual update", () => {
    const markup = renderContent({ refreshFailed: true });

    expect(markup).toContain("Rotación voluntaria");
    expect(markup).toContain(
      "No se pudo actualizar. Se mantiene la última información disponible.",
    );
  });

  it("renders the single neutral empty state", () => {
    const markup = renderContent({
      experience: { ...experience, sections: [] },
    });

    expect(markup).toContain("No hay observaciones empresariales para mostrar.");
    expect(markup).toContain('role="status"');
  });
});

describe("ExperienceLoadState", () => {
  it.each([
    ["forbidden", "No tienes acceso a esta vista."],
    ["not-found", "Esta vista no está disponible."],
    ["unavailable", "No se pudo cargar la información empresarial."],
  ] as const)("renders safe %s copy", (state, copy) => {
    const markup = renderToStaticMarkup(
      <ExperienceLoadState state={state} onRetry={vi.fn()} />,
    );

    expect(markup).toContain(copy);
    expect(markup).toContain('role="alert"');
    expect(markup).not.toContain("sensitive backend detail");
  });

  it("renders a general loading indicator without section skeletons", () => {
    const markup = renderToStaticMarkup(<ExperienceLoadState state="loading" />);

    expect(markup).toContain("Cargando información empresarial");
    expect(markup).not.toContain("skeleton");
  });
});
